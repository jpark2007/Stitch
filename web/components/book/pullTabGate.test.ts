/**
 * The pull tab's gate (pullTabGate.ts): when a tab is out of its slit, and
 * when it takes a pull.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The bug: a tab bound to one shot was drawn, eased out under the pointer
 * and wiggled at the end in every shot, but its press was swallowed outside
 * its own shot (and while the reveal was under 0.9, after the scene had
 * started at 0.85). You could see it, grab it, and nothing happened. Now a
 * tab is out exactly when it pulls.
 *
 * The module is imported by a computed path so tsc does not ask for a `.ts`
 * import extension (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Gate = typeof import("./pullTabGate");
const paths = ["./pullTabGate.ts"];
const g: Gate = await import(paths[0]);

type Input = Parameters<Gate["tabGate"]>[0];
const OPEN = 0.85;
const input = (over: Partial<Input> = {}): Input => ({
  tabShot: null,
  frameShot: null,
  reveal: 1,
  openAt: OPEN,
  held: false,
  away: 0,
  ...over,
});

describe("tabGate", () => {
  it("the bike: each tab is out and pulls only in its own shot", () => {
    const ride = (frameShot: string) => g.tabGate(input({ tabShot: "s1", frameShot }));
    const bell = (frameShot: string) => g.tabGate(input({ tabShot: "s2", frameShot }));
    assert.deepEqual(ride("s1"), { out: true, grabbable: true });
    assert.deepEqual(bell("s1"), { out: false, grabbable: false });
    assert.deepEqual(bell("s2"), { out: true, grabbable: true });
    assert.deepEqual(ride("s2"), { out: false, grabbable: false });
    // After the end the frame keeps the last shot: bell stays live, ride home.
    assert.equal(bell("s2").grabbable, true, "end hold: the last shot's tab pulls");
    assert.equal(ride("s2").out, false, "end hold: the first shot's tab rests in its slit");
  });

  it("a tab with no shot is out in any frame, and any tab before the first shot", () => {
    for (const frameShot of [null, undefined, "s1", "s2"]) assert.equal(g.tabGate(input({ tabShot: null, frameShot })).out, true);
    for (const tabShot of ["s1", "s2"]) assert.equal(g.tabGate(input({ tabShot, frameShot: null })).out, true);
  });

  it("pulls from the moment the play gate opens (the old 0.9 guard swallowed 0.85..0.9)", () => {
    assert.equal(g.tabGate(input({ reveal: 0.85 })).grabbable, true);
    assert.equal(g.tabGate(input({ reveal: 0.87 })).grabbable, true);
    assert.equal(g.tabGate(input({ reveal: 0.84 })).grabbable, false);
  });

  it("a held tab is never yanked away when the shot changes under the hand", () => {
    const held = g.tabGate(input({ tabShot: "s1", frameShot: "s2", held: true }));
    assert.deepEqual(held, { out: true, grabbable: true });
  });

  it("a card still sliding out does not pull yet", () => {
    assert.equal(g.tabGate(input({ away: 0.6 })).grabbable, false);
    assert.equal(g.tabGate(input({ away: 0 })).grabbable, true);
  });

  it("is never shown dead, and works every time it is shown", () => {
    for (const tabShot of [null, "s1", "s2"])
      for (const frameShot of [null, "s1", "s2"])
        for (const reveal of [0, 0.5, 0.84, 0.85, 1])
          for (const held of [true, false])
            for (const away of [0, 0.3, 0.6, 1]) {
              const i = input({ tabShot, frameShot, reveal, held, away });
              const r = g.tabGate(i);
              const at = JSON.stringify(i);
              if (r.grabbable) assert.equal(r.out, true, `grabbable while home: ${at}`);
              if (r.out && reveal >= OPEN && away === 0) assert.equal(r.grabbable, true, `shown out but dead: ${at}`);
            }
  });
});

describe("stepTabAway", () => {
  const run = (from: number, out: boolean, dt = 1 / 60) => {
    const seen = [from];
    let a = from;
    for (let i = 0; i < 600 && a !== (out ? 0 : 1); i++) seen.push((a = g.stepTabAway(a, out, dt)));
    return seen;
  };

  it("slides home and out, monotone, arriving exactly", () => {
    const home = run(0, false);
    const out = run(1, true);
    assert.equal(home.at(-1), 1);
    assert.equal(out.at(-1), 0);
    for (let i = 1; i < home.length; i++) assert.ok(home[i] > home[i - 1], `home step ${i}`);
    for (let i = 1; i < out.length; i++) assert.ok(out[i] < out[i - 1], `out step ${i}`);
    assert.ok(home.length / 60 < 0.75, `slides in about half a second (${(home.length / 60).toFixed(2)} s)`);
    assert.ok(home.length > 10, "slides, does not pop");
  });

  it("clamps a huge frame time", () => {
    assert.equal(g.stepTabAway(0, false, 10), g.stepTabAway(0, false, 0.05));
    assert.ok(g.stepTabAway(0, false, 10) < 0.25);
  });
});

describe("printEats", () => {
  it("a home tab's print eats a press on the open page (the bell slit turned the page)", () => {
    const bell = g.tabGate(input({ tabShot: "s2", frameShot: "s1" }));
    assert.equal(g.printEats({ grabbable: bell.grabbable, reveal: 1, openAt: OPEN }), true);
  });

  it("never over a tab that pulls, nor while the page is opening or turning", () => {
    assert.equal(g.printEats({ grabbable: true, reveal: 1, openAt: OPEN }), false);
    assert.equal(g.printEats({ grabbable: false, reveal: 0.5, openAt: OPEN }), false);
  });

  it("the print covers the slit's cut and the label under it, not the page above", () => {
    const [slit, label] = g.printRects(150, 76, "Pull to ring the bell");
    assert.ok(slit.x0 < 0 && slit.x1 > 150, "slit to past the grip");
    assert.ok(slit.y0 <= -(76 / 2 + 7) && slit.y0 > -60, "just the slit's cut above the card");
    assert.ok(label.y0 > slit.y0 && label.y1 >= 76 / 2 + 46, "down to the label's baseline");
    // Right-aligned under the grip: "Pull to ring the bell" runs ~260 px left of it.
    assert.ok(label.x0 <= 154 - 260 && label.x1 >= 154 + 38, "the label, and its arrow");
  });

  it("a long label wraps in two lines: the print is taller, not wider than the wrap", () => {
    const [, one] = g.printRects(150, 76, "Pull to ring the bell");
    const [, two] = g.printRects(150, 76, "Pull to push Sam off down the street");
    assert.ok(two.y1 - two.y0 >= one.y1 - one.y0 + 30);
    assert.ok(two.x1 - two.x0 <= 360 + 40 + 12 + 48);
  });
});

describe("hitPad", () => {
  it("holds a steady few px on screen between its bounds", () => {
    const at = 0.0025;
    assert.ok(Math.abs(g.hitPad(at) / at - g.HIT_PAD_PX) < 1e-9);
    assert.ok(g.hitPad(0.004) > g.hitPad(0.002), "a farther view: more world units");
  });

  it("is clamped: never a slab of the turn band, never nothing", () => {
    assert.equal(g.hitPad(1), g.HIT_PAD_MAX);
    assert.equal(g.hitPad(1e-6), g.HIT_PAD_MIN);
    for (const bad of [0, -1, Number.NaN, Infinity]) assert.equal(g.hitPad(bad), g.HIT_PAD_MIN);
  });

  it("worldPerPx: the view's height in world units at that distance, per px", () => {
    // 90° fov: the view is 2·d tall.
    assert.ok(Math.abs(g.worldPerPx(2, 90, 800) - 4 / 800) < 1e-12);
    assert.ok(Math.abs(g.worldPerPx(4, 90, 800) - 2 * g.worldPerPx(2, 90, 800)) < 1e-12);
  });
});

describe("endNudge", () => {
  const base = { ended: true, grabbable: true, pulled: false, held: false, nudged: false };

  it("nudges once at the end, for a tab that can be pulled and has not been", () => {
    assert.equal(g.endNudge(base), true);
    assert.equal(g.endNudge({ ...base, nudged: true }), false);
    assert.equal(g.endNudge({ ...base, pulled: true }), false);
    assert.equal(g.endNudge({ ...base, held: true }), false);
    assert.equal(g.endNudge({ ...base, ended: false }), false);
  });

  it("never wiggles a tab that cannot be pulled (the dead s1 tab at the end)", () => {
    const ride = g.tabGate(input({ tabShot: "s1", frameShot: "s2" }));
    assert.equal(g.endNudge({ ...base, grabbable: ride.grabbable }), false);
  });
});
