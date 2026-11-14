/**
 * Click-to-explain, the pure half (explain.ts): which targets a stage can
 * explain, how a note is set and where it stands so it stays on screen,
 * which note is open after each click, key and turn, and the pop-up's motion.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The module is imported by a computed path so tsc does not ask for a `.ts`
 * import extension (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Explain = typeof import("./explain");
const paths = ["./explain.ts"];
const x: Explain = await import(paths[0]);

const people = [
  { id: "dad", name: "David", character: { color: "#5b82a6", build: "average" as const } },
  { id: "mom", name: "Ana", character: { color: "#8b5a7c", build: "average" as const } },
];
const props = [{ id: "hymn_books", name: "Hymn books", color: "#7a4a2a" }];
const cast = { puppets: ["dad", "mom"], props: ["hymn_books"] };

describe("parseTarget", () => {
  it("reads puppet and prop targets", () => {
    assert.deepEqual(x.parseTarget("puppet:dad"), { kind: "puppet", id: "dad", target: "puppet:dad" });
    assert.deepEqual(x.parseTarget("prop:hymn_books"), { kind: "prop", id: "hymn_books", target: "prop:hymn_books" });
    assert.deepEqual(x.parseTarget("  prop:red-bike "), { kind: "prop", id: "red-bike", target: "prop:red-bike" });
  });

  it("ignores anything else", () => {
    for (const bad of ["", "dad", "puppet:", "set:church.layer0", "camera", "puppet:dad.hand_r", "prop:a b", "Puppet:dad", null, 3, {}, undefined])
      assert.equal(x.parseTarget(bad), null, String(bad));
  });
});

describe("explainItems", () => {
  it("matches the sample's three explanations to the cast, headed by name", () => {
    const items = x.explainItems(
      [
        { target: "puppet:dad", text: "Dad was 26." },
        { target: "puppet:mom", text: "Mom sang in the choir." },
        { target: "prop:hymn_books", text: "The hymn books Mom dropped." },
      ],
      { people, props },
      cast,
    );
    assert.deepEqual(
      items.map((i) => [i.target, i.title, i.color]),
      [
        ["puppet:dad", "David", "#5b82a6"],
        ["puppet:mom", "Ana", "#8b5a7c"],
        ["prop:hymn_books", "Hymn books", "#7a4a2a"],
      ],
    );
  });

  it("drops targets the stage does not cast, malformed ones, empty texts and repeats", () => {
    const items = x.explainItems(
      [
        { target: "puppet:grandpa_joseph", text: "Not in this scene." },
        { target: "prop:umbrella", text: "Not tracked here." },
        { target: "nonsense", text: "x" },
        { target: "puppet:dad", text: "   " },
        { target: "puppet:mom", text: "First." },
        { target: "puppet:mom", text: "Second." },
        null as never,
        { target: 7, text: "x" } as never,
        { target: "prop:hymn_books" } as never,
      ],
      { people, props },
      cast,
    );
    assert.deepEqual(items.map((i) => [i.target, i.text]), [["puppet:mom", "First."]]);
  });

  it("names cast the scene data does not describe from their id", () => {
    const items = x.explainItems([{ target: "puppet:grandma_rosa", text: "Rosa." }], { people: [], props: [] }, { puppets: ["grandma_rosa"], props: [] });
    assert.equal(items[0].title, "Grandma Rosa");
    assert.equal(items[0].color, null);
  });

  it("has nothing to explain without explanations", () => {
    assert.deepEqual(x.explainItems(null, { people, props }, cast), []);
    assert.deepEqual(x.explainItems(undefined, { people, props }, cast), []);
    assert.deepEqual(x.explainItems("x" as never, { people, props }, cast), []);
  });

  it("cuts a very long text at a word, with an ellipsis", () => {
    const long = "word ".repeat(400);
    const [item] = x.explainItems([{ target: "puppet:dad", text: long }], { people, props }, cast);
    assert.ok(item.text.length <= x.MAX_TEXT + 1);
    assert.ok(item.text.endsWith("word…"));
  });
});

describe("onSet", () => {
  it("is on inside the wings and off beyond them", () => {
    assert.equal(x.onSet(0), true);
    assert.equal(x.onSet(-0.9), true);
    assert.equal(x.onSet(0.97), false);
    assert.equal(x.onSet(-1.3), false);
    assert.equal(x.onSet(Number.NaN), false);
  });
});

/** A handwriting font where every character is half the type size wide. */
const measure: Parameters<Explain["layoutNote"]>[2] = (t, px) => t.length * px * 0.5;

describe("layoutNote", () => {
  it("keeps a short note narrow, with its heading", () => {
    const l = x.layoutNote("David", "Dad was 26.", measure);
    assert.equal(l.title, "David");
    assert.deepEqual(l.lines, ["Dad was 26."]);
    assert.equal(l.width, x.NOTE_LAYOUT.minW);
    assert.equal(l.font, x.NOTE_LAYOUT.font);
  });

  it("wraps a 300+ character text, widening then shrinking the type, within its limits", () => {
    const text =
      "The hymn books Mom dropped on the steps that Sunday morning, a whole armful of them, green cloth covers gone soft at the corners. Dad says it is the best thing that ever fell at his feet, and he still has one of them, the one with her name pencilled inside the cover, on the shelf by the front door, where anyone coming in can see it.";
    assert.ok(text.length > 300);
    const short = x.layoutNote("Hymn books", "A line.", measure);
    const l = x.layoutNote("Hymn books", text, measure);
    assert.ok(l.lines.length > 1);
    assert.ok(l.lines.length <= x.NOTE_LAYOUT.maxLines);
    assert.ok(l.width <= x.NOTE_LAYOUT.maxW && l.width > short.width);
    assert.ok(l.height > short.height);
    assert.ok(l.font >= x.NOTE_LAYOUT.minFont && l.font <= x.NOTE_LAYOUT.font);
    for (const line of l.lines) assert.ok(measure(line, l.font) <= l.width - 2 * l.pad + 1e-9, line);
    // Every word is set, in order.
    assert.equal(l.lines.join(" "), text);
  });

  it("cuts what will not fit with an ellipsis", () => {
    const l = x.layoutNote("Mom", "and then ".repeat(120), measure);
    assert.equal(l.lines.length, x.NOTE_LAYOUT.maxLines);
    assert.ok(l.lines.at(-1)!.endsWith("…"));
    assert.equal(l.font, x.NOTE_LAYOUT.minFont);
  });

  it("names a note with no heading", () => {
    assert.equal(x.layoutNote("  ", "x", measure).title, "Why it matters");
  });
});

describe("writeSchedule", () => {
  it("writes word after word, longer words taking longer, a beat after a full stop", () => {
    const s = x.writeSchedule(["Dad", "was", "twenty-six.", "He"]);
    for (let i = 1; i < s.length; i++) assert.ok(s[i].delay >= s[i - 1].delay + s[i - 1].dur, JSON.stringify(s));
    assert.ok(s[2].dur > s[1].dur);
    const afterStop = s[3].delay - (s[2].delay + s[2].dur);
    const afterWord = s[1].delay - (s[0].delay + s[0].dur);
    assert.ok(afterStop > afterWord);
  });

  it("never keeps the reader waiting longer than its total", () => {
    const words = Array.from({ length: 120 }, (_, i) => `word${i}`);
    const s = x.writeSchedule(words, { ...x.WRITE, total: 1500 });
    const end = s[s.length - 1].delay + s[s.length - 1].dur;
    assert.ok(end <= 1500 + 30, String(end));
    assert.equal(s.length, 120);
  });
});

describe("writePlan / writeAt", () => {
  const l = x.layoutNote("David", "Dad was 26 and had only come to church that Sunday because his mother asked.", measure);
  const plan = x.writePlan(l, measure);

  it("has a row for the heading and each line, and a step per word", () => {
    assert.equal(plan.rowW.length, 1 + l.lines.length);
    const words = 1 + l.lines.join(" ").split(" ").length;
    assert.equal(plan.row.length, words);
    // The heading's stitch can reach past its word.
    assert.ok(plan.rowW[0] >= x.underlineEnd(measure("David", l.titleFont), l) - 1e-9);
  });

  it("moves the pen forward, row by row, and ends with everything written", () => {
    assert.equal(x.writeAt(plan, -5), -1);
    let last = -1;
    for (let ms = 0; ms < plan.total; ms += 7) {
      const p = x.writeAt(plan, ms);
      assert.ok(p >= last - 1e-9, `${ms}: ${p} < ${last}`);
      assert.ok(p <= l.lines.length + 1);
      last = p;
    }
    assert.equal(x.writeAt(plan, plan.total), x.WRITTEN);
    assert.ok(plan.total <= x.WRITE.total + 30);
  });

  it("finishes each row before the next starts", () => {
    for (let i = 1; i < plan.row.length; i++) {
      if (plan.row[i] !== plan.row[i - 1]) {
        assert.equal(plan.row[i], plan.row[i - 1] + 1);
        assert.equal(plan.f0[i], 0);
      }
      assert.ok(plan.f1[i] <= 1 && plan.f0[i] <= plan.f1[i]);
    }
  });

  it("writes an empty note at once", () => {
    const empty = x.writePlan({ ...l, title: "", lines: [] }, measure);
    assert.equal(x.writeAt(empty, 0), x.WRITTEN);
  });
});

/**
 * A camera looking at the stage from the front: stage x maps linearly to
 * screen x, and a note standing at x, h tall, covers [x - w/2, x + w/2] by
 * [floor - h, floor] (px per stage unit `k`), its width `aspect` × height.
 */
function camera(k: number, floor: number, cx: number, aspect: number) {
  return (sx: number, h: number, d: number, out: Parameters<Explain["placeNote"]>[1]["rect"]) => {
    // Each depth slot stands nearer the reader: lower on screen and a little larger.
    const kk = k * (1 + 0.1 * d);
    const w = h * aspect * kk;
    out.x0 = cx + sx * kk - w / 2;
    out.x1 = cx + sx * kk + w / 2;
    out.y1 = floor + 70 * d;
    out.y0 = out.y1 - h * kk;
    return out;
  };
}

const inside = (r: { x0: number; y0: number; x1: number; y1: number }, v: typeof r) =>
  r.x0 >= v.x0 - 1e-6 && r.x1 <= v.x1 + 1e-6 && r.y0 >= v.y0 - 1e-6 && r.y1 <= v.y1 + 1e-6;
const overlaps = (a: { x0: number; y0: number; x1: number; y1: number }, b: typeof a) =>
  Math.min(a.x1, b.x1) > Math.max(a.x0, b.x0) && Math.min(a.y1, b.y1) > Math.max(a.y0, b.y0);

describe("placeNote", () => {
  const desktop = { view: x.rect(16, 64, 1264, 784), k: 400, floor: 600, cx: 640 };
  const base = (over: Partial<Parameters<Explain["placeNote"]>[0]> = {}) => ({
    target: x.rect(600, 380, 680, 590),
    view: desktop.view,
    avoid: [],
    rectAt: camera(desktop.k, desktop.floor, desktop.cx, 0.9),
    targetX: 0,
    xMin: -1.05,
    xMax: 1.05,
    wantPx: 200,
    minPx: 120,
    maxH: 0.8,
    prevX: null,
    ...over,
  });

  it("stands beside the target at the wanted size, on screen and off the target", () => {
    const p = x.placeNote(base(), x.placement());
    assert.equal(p.fits, true);
    assert.ok(Math.abs(p.rect.y1 - p.rect.y0 - 200) < 1e-6);
    assert.ok(inside(p.rect, desktop.view));
    assert.ok(!overlaps(p.rect, base().target));
    // Close beside it, not across the stage.
    assert.ok(Math.abs(p.x) < 0.5, String(p.x));
  });

  it("goes to the side with room when the target stands at an edge", () => {
    const right = x.placeNote(base({ target: x.rect(980, 380, 1060, 590), targetX: 0.9 }), x.placement());
    assert.equal(right.side, -1);
    assert.ok(inside(right.rect, desktop.view));
    const left = x.placeNote(base({ target: x.rect(220, 380, 300, 590), targetX: -0.9 }), x.placement());
    assert.equal(left.side, 1);
    assert.ok(inside(left.rect, desktop.view));
  });

  it("keeps off the rest of the cast when there is room", () => {
    // Someone stands just left of the target: the note goes right.
    const other = x.rect(480, 380, 560, 590);
    const p = x.placeNote(base({ others: [other] }), x.placement());
    assert.ok(!overlaps(p.rect, other), JSON.stringify(p.rect));
    assert.ok(!overlaps(p.rect, base().target));
    assert.equal(p.side, 1);
  });

  it("would rather shrink or step toward the reader than hide its target", () => {
    // A phone-sized view where a full-size note beside the target does not fit.
    const view = x.rect(16, 64, 374, 780);
    const rectAt = camera(150, 470, 195, 1.3);
    const target = x.rect(205, 330, 245, 465);
    const p = x.placeNote(base({ view, rectAt, target, targetX: 0.2, wantPx: 190, minPx: 110, depths: 3 }), x.placement());
    assert.equal(p.fits, true);
    assert.ok(inside(p.rect, view));
    const hidden = Math.max(0, Math.min(p.rect.x1, target.x1) - Math.max(p.rect.x0, target.x0)) * Math.max(0, Math.min(p.rect.y1, target.y1) - Math.max(p.rect.y0, target.y0));
    assert.ok(hidden / ((target.x1 - target.x0) * (target.y1 - target.y0)) < 0.25, JSON.stringify(p));
  });

  it("shrinks to fit a phone, and stays on screen", () => {
    // 390 px wide, the stage page filling it.
    const view = x.rect(16, 64, 374, 780);
    const rectAt = camera(170, 520, 195, 0.9);
    const p = x.placeNote(base({ view, rectAt, target: x.rect(170, 420, 220, 515), wantPx: 300, minPx: 120 }), x.placement());
    assert.equal(p.fits, true);
    assert.ok(inside(p.rect, view), JSON.stringify(p.rect));
    assert.ok(p.rect.y1 - p.rect.y0 < 300);
    assert.ok(p.rect.y1 - p.rect.y0 >= 120 - 1e-6);
  });

  it("never covers the caption entirely", () => {
    // A caption just big enough to hide behind a note anywhere near the target.
    const caption = x.rect(560, 520, 720, 560);
    const p = x.placeNote(base({ avoid: [caption] }), x.placement());
    assert.equal(p.fits, true);
    const ox = Math.max(0, Math.min(p.rect.x1, caption.x1) - Math.max(p.rect.x0, caption.x0));
    assert.ok(ox < caption.x1 - caption.x0);
  });

  it("keeps wholly clear of the caption when any spot allows, shrinking or taking a costlier slot first", () => {
    // A phone: the caption just under the set, the floor slots all reach it.
    const view = x.rect(16, 64, 374, 780);
    const caption = x.rect(40, 488, 350, 540);
    // Slot 1 stands raised: higher on screen, clear of the words.
    const floor = camera(150, 500, 195, 1.3);
    const rectAt = (sx: number, h: number, d: number, out: Parameters<Explain["placeNote"]>[1]["rect"]) => {
      floor(sx, h, 0, out);
      if (d === 1) {
        out.y0 -= 120;
        out.y1 -= 120;
      }
      return out;
    };
    const target = x.rect(90, 380, 130, 495);
    const p = x.placeNote(
      base({ view, rectAt, target, targetX: -0.6, wantPx: 160, minPx: 110, avoid: [caption], depths: 2, slotCost: [0, 0.4] }),
      x.placement(),
    );
    assert.equal(p.fits, true);
    assert.ok(!overlaps(p.rect, caption), JSON.stringify(p));
    assert.equal(p.depth, 1);
    // With no clear spot at all, it still stands, as little over the words as it can.
    const wide = x.rect(0, 0, 400, 2000);
    const q = x.placeNote(base({ view, rectAt: floor, target, targetX: -0.6, wantPx: 160, minPx: 110, avoid: [wide] }), x.placement());
    assert.ok(inside(q.rect, view));
  });

  it("would rather stand in front of the cast than behind it when either covers them", () => {
    // Both slots stand the note in the same place on screen, over someone; slot 1 is
    // behind them (cheaper, but its words would be hidden).
    const floor = camera(desktop.k, desktop.floor, desktop.cx, 0.9);
    const same = (sx: number, h: number, _d: number, out: Parameters<Explain["placeNote"]>[1]["rect"]) => floor(sx, h, 0, out);
    // A crowd everywhere: small cutouts tiling the screen.
    const crowd: ReturnType<typeof x.rect>[] = [];
    for (let i = 0; i < 16; i++) for (let j = 0; j < 10; j++) crowd.push(x.rect(i * 80, j * 80, i * 80 + 80, j * 80 + 80));
    const p = x.placeNote(base({ rectAt: same, others: crowd, depths: 2, slotCost: [0.3, 0], behind: [false, true] }), x.placement());
    assert.equal(p.depth, 0);
    const q = x.placeNote(base({ rectAt: same, others: crowd, depths: 2, slotCost: [0.3, 0] }), x.placement());
    assert.equal(q.depth, 1);
  });

  it("prefers the cheaper slot when both are clear", () => {
    const p = x.placeNote(base({ depths: 3, slotCost: [0.5, 0, 0.5] }), x.placement());
    assert.equal(p.depth, 1);
  });

  it("returns the least-bad spot, at its smallest, when nothing fits", () => {
    const view = x.rect(0, 0, 100, 100);
    const p = x.placeNote(base({ view, wantPx: 400, minPx: 300 }), x.placement());
    assert.equal(p.fits, false);
    assert.ok(Math.abs(p.rect.y1 - p.rect.y0 - 300) < 1e-6);
  });

  it("caps the note's height on the page", () => {
    const p = x.placeNote(base({ wantPx: 2000 }), x.placement());
    assert.ok(p.h <= 0.8 + 1e-9);
  });

  it("grows past its height cap rather than set its words too small to read", () => {
    // A small stage (60 px per unit): a cap of 0.8 units would be 48 px.
    const view = x.rect(0, 0, 400, 800);
    const p = x.placeNote(base({ view, rectAt: camera(60, 700, 200, 0.9), target: x.rect(190, 670, 200, 700), wantPx: 300, minPx: 150 }), x.placement());
    assert.ok(p.rect.y1 - p.rect.y0 >= 150 - 1e-6, JSON.stringify(p.rect));
  });

  it("sizes a card foreshortened by a camera looking down by its mean scale, not its squashed height", () => {
    // Seen from above: a standing card shows half its height (0.9 aspect, 400 px per unit across).
    const squash = (sx: number, h: number, _d: number, out: Parameters<Explain["placeNote"]>[1]["rect"]) => {
      const w = h * 0.9 * 400;
      out.x0 = 640 + sx * 400 - w / 2;
      out.x1 = out.x0 + w;
      out.y1 = 600;
      out.y0 = 600 - h * 200;
      return out;
    };
    const byHeight = x.placeNote(base({ rectAt: squash, wantPx: 200, minPx: 200 }), x.placement());
    const byMean = x.placeNote(base({ rectAt: squash, wantPx: 200, minPx: 200, aspect: 0.9 }), x.placement());
    // Face on it would be 200 px tall at 1 unit per 200 px; the mean scale (283 px per unit) makes it smaller.
    assert.ok(Math.abs(byHeight.h - 1) < 1e-6, String(byHeight.h));
    assert.ok(Math.abs(byMean.h - 200 / Math.sqrt(200 * 400)) < 1e-6, String(byMean.h));
  });

  it("stays where it was while that still works", () => {
    const first = x.placeNote(base(), x.placement());
    const again = x.placeNote(base({ prevX: first.x, target: x.rect(602, 380, 682, 590) }), x.placement());
    assert.ok(Math.abs(again.x - first.x) < 0.11, `${first.x} -> ${again.x}`);
  });

  it("allocates nothing: it writes into the placement it is given", () => {
    const out = x.placement();
    const r = out.rect;
    assert.equal(x.placeNote(base(), out), out);
    assert.equal(out.rect, r);
  });
});

describe("explainStep", () => {
  const can = (t: string) => t !== "puppet:offstage";

  it("opens a target, closes it on a second click, swaps to another", () => {
    let s = x.explainStep(null, { type: "target", target: "puppet:dad" }, can);
    assert.deepEqual(s, { open: "puppet:dad", closed: null });
    s = x.explainStep(s.open, { type: "target", target: "prop:hymn_books" }, can);
    assert.deepEqual(s, { open: "prop:hymn_books", closed: null });
    s = x.explainStep(s.open, { type: "target", target: "prop:hymn_books" }, can);
    assert.deepEqual(s, { open: null, closed: "toggle" });
  });

  it("closes on a click elsewhere, the note, Escape, a turn, a telling, or its target leaving", () => {
    for (const type of ["elsewhere", "note", "escape", "turn", "telling", "gone"] as const) {
      assert.deepEqual(x.explainStep("puppet:mom", { type }, can), { open: null, closed: type });
      assert.deepEqual(x.explainStep(null, { type }, can), { open: null, closed: null });
    }
  });

  it("does not open what cannot be explained now", () => {
    assert.deepEqual(x.explainStep(null, { type: "target", target: "puppet:offstage" }, can), { open: null, closed: null });
    assert.deepEqual(x.explainStep("puppet:dad", { type: "open", target: "puppet:offstage" }, can), { open: "puppet:dad", closed: null });
  });

  it("opens and closes from the test hook", () => {
    assert.deepEqual(x.explainStep(null, { type: "open", target: "puppet:dad" }, can), { open: "puppet:dad", closed: null });
    assert.deepEqual(x.explainStep("puppet:dad", { type: "open", target: null }, can), { open: null, closed: "probe" });
    assert.deepEqual(x.explainStep(null, { type: "open", target: null }, can), { open: null, closed: null });
  });

  it("hands the scene back to play unless the page turned or a telling was chosen", () => {
    for (const r of ["toggle", "note", "elsewhere", "escape", "gone", "probe"] as const) assert.equal(x.resumesAfter(r), true, r);
    assert.equal(x.resumesAfter("turn"), false);
    assert.equal(x.resumesAfter("telling"), false);
  });
});

describe("motion", () => {
  it("rises from flat, past standing once, and settles exactly standing", () => {
    assert.equal(x.noteRise(0), 0);
    let peak = 0;
    for (let ms = 0; ms <= x.RISE_MS; ms += 10) peak = Math.max(peak, x.noteRise(ms));
    assert.ok(peak > 1 && peak < 1.1, String(peak));
    assert.equal(x.noteRise(x.RISE_MS), 1);
    assert.equal(x.noteRise(x.RISE_MS + 500), 1);
    // Mostly up in its first third.
    assert.ok(x.noteRise(x.RISE_MS * 0.3) > 0.8);
  });

  it("folds back down to flat", () => {
    assert.equal(x.noteFold(0), 1);
    assert.equal(x.noteFold(x.FOLD_MS), 0);
    assert.ok(x.noteFold(x.FOLD_MS * 0.5) > 0 && x.noteFold(x.FOLD_MS * 0.5) < 1);
  });

  it("sews the thread once the card is half up, and unpicks it first on the way down", () => {
    assert.equal(x.sewAt(0, false), 0);
    assert.equal(x.sewAt(x.RISE_MS, false), 1);
    assert.equal(x.sewAt(0, true), 1);
    assert.equal(x.sewAt(x.FOLD_MS * 0.5, true), 0);
  });

  it("eases the lift to its goal and stops there", () => {
    let v = 0;
    for (let i = 0; i < 60; i++) v = x.stepLift(v, 1, 16);
    assert.equal(v, 1);
    assert.ok(x.stepLift(0, 1, 16) > 0 && x.stepLift(0, 1, 16) < 0.5);
    assert.equal(x.stepLift(0.5, 0.5, 16), 0.5);
  });
});
