/**
 * Unit tests for where person names go (names.ts), on their own and through
 * the real renderer for families of 9 to 30 people on a phone and a laptop:
 *
 *   cd web && node --no-warnings --test "components/threads/*.test.ts"
 *
 * The renderer draws on a stub 2D context: every call is a no-op except
 * measureText, which reads the font size the renderer set.
 */
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";

// renderer.ts imports its siblings without extensions: add ".ts" for node.
// (registerHooks is newer than this repo's @types/node, hence the local type.)
type Next = (specifier: string, context: object) => unknown;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: { parentURL?: string }, next: Next): unknown }): void;
};
registerHooks({
  resolve(specifier, context, next) {
    const fromTs = typeof context.parentURL === "string" && context.parentURL.endsWith(".ts");
    if (fromTs && /^\.\.?\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier)) return next(`${specifier}.ts`, context);
    return next(specifier, context);
  },
});

// Path2D is a browser global the axis uses.
(globalThis as Record<string, unknown>).Path2D ??= class {
  moveTo() {}
  lineTo() {}
};

const namesPath = "./names.ts";
const rendererPath = "./renderer.ts";
const modelPath = "./model.ts";
const cameraPath = "./camera.ts";
const { NAME_GUTTER, NAME_MIN_PX, insideGutter, nameBox, placeNames }: typeof import("./names") = await import(namesPath);
const { drawFrame, makeLayout, nameFontPx, namesInline }: typeof import("./renderer") = await import(rendererPath);
const { buildModel }: typeof import("./model") = await import(modelPath);
const { clampCam, initialCamera }: typeof import("./camera") = await import(cameraPath);
type ThreadPerson = import("./model").ThreadPerson;
type ThreadMoment = import("./model").ThreadMoment;
type NameBox = import("./names").NameBox;

const NOW = 2026;

/* ---------------- placeNames on its own ---------------- */

const SIZE = 16;

function overlap(a: NameBox, b: NameBox): boolean {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
}

test("two names wanting the same spot: the higher rank gets it, the other slides to its next spot", () => {
  const placed = placeNames(
    [
      { strand: 0, width: 80, rank: 1, spots: [{ x: 20, y: 100 }, { x: 140, y: 100 }] },
      { strand: 1, width: 80, rank: 5, spots: [{ x: 20, y: 110 }] },
    ],
    { width: 390, size: SIZE },
  );
  assert.deepEqual(
    placed.map((p) => [p.strand, p.x]),
    [
      [1, 20],
      [0, 140],
    ],
  );
});

test("a name with nowhere left is dropped rather than drawn over another", () => {
  const placed = placeNames(
    [
      { strand: 0, width: 80, rank: 9, spots: [{ x: 20, y: 100 }] },
      { strand: 1, width: 80, rank: 1, spots: [{ x: 30, y: 104 }] },
    ],
    { width: 390, size: SIZE },
  );
  assert.deepEqual(
    placed.map((p) => p.strand),
    [0],
  );
});

test("spots that cross the 16px gutter on either side are never used", () => {
  const placed = placeNames(
    [
      { strand: 0, width: 80, rank: 1, spots: [{ x: 10, y: 100 }, { x: 300, y: 100 }, { x: 200, y: 100 }] },
      { strand: 1, width: 80, rank: 1, spots: [{ x: 1380, y: 200 }] },
    ],
    { width: 390, size: SIZE },
  );
  assert.deepEqual(
    placed.map((p) => [p.strand, p.x]),
    [[0, 200]],
  );
  for (const p of placed) assert.ok(insideGutter(p, 390));
});

test("the followed person's name is always written, first, and held inside the gutter", () => {
  const placed = placeNames(
    [
      { strand: 0, width: 80, rank: 1000, spots: [{ x: 280, y: 100 }] },
      // Only a forced spot, half off the right edge.
      { strand: 1, width: 80, rank: 0, pinned: true, spots: [{ x: 350, y: 102, forced: true }] },
    ],
    { width: 390, size: SIZE },
  );
  const pin = placed.find((p) => p.strand === 1)!;
  assert.ok(pin, "pinned name shown");
  assert.equal(pin.forced, true);
  assert.equal(pin.x1, 390 - NAME_GUTTER);
  assert.ok(placed.every((p) => insideGutter(p, 390)));
  // It went first, so the other name gave way.
  assert.equal(placed.find((p) => p.strand === 0), undefined);
});

test("forced spots are only for the followed person", () => {
  const placed = placeNames([{ strand: 0, width: 80, rank: 1, spots: [{ x: 20, y: 100, forced: true }] }], { width: 390, size: SIZE });
  assert.equal(placed.length, 0);
});

test("a name shown last frame keeps its place against a slightly higher rank", () => {
  const reqs = [
    { strand: 0, width: 80, rank: 3, spots: [{ x: 20, y: 100 }] },
    { strand: 1, width: 80, rank: 4, spots: [{ x: 24, y: 100 }] },
  ];
  const shown = new Uint8Array([1, 0]);
  assert.deepEqual(
    placeNames(reqs, { width: 390, size: SIZE, shown }).map((p) => p.strand),
    [0],
  );
  assert.deepEqual([...shown], [1, 0]);
  // Without the history the higher rank wins.
  assert.deepEqual(
    placeNames(reqs, { width: 390, size: SIZE }).map((p) => p.strand),
    [1],
  );
});

/** A level thread from x 0 to `end` at baseline y: clean anywhere along it. */
const levelFit = (y: number, end = 1e9, w = 80) => (x: number) => (x + w <= end ? y : NaN);

test("a name holding a place stays there, rather than hopping to its best spot", () => {
  const placed = placeNames(
    [{ strand: 0, width: 80, rank: 1, spots: [{ x: 20, y: 100 }, { x: 140, y: 100 }], held: 97.5, fit: levelFit(100) }],
    { width: 390, size: SIZE },
  );
  assert.deepEqual(
    placed.map((p) => [p.x, p.y]),
    [[97.5, 100]],
  );
});

test("held past the left gutter, a name rides the margin", () => {
  const placed = placeNames(
    [{ strand: 0, width: 80, rank: 1, spots: [{ x: 140, y: 100 }], held: 9, fit: levelFit(100) }],
    { width: 390, size: SIZE },
  );
  assert.equal(placed[0].x, NAME_GUTTER);
});

test("a held name another name runs into is nudged along behind it, not sent elsewhere", () => {
  const placed = placeNames(
    [
      // Riding the margin, and higher priority.
      { strand: 0, width: 60, rank: 9, spots: [{ x: NAME_GUTTER, y: 100 }], held: NAME_GUTTER - 1, fit: levelFit(100) },
      // Was at 80; the rider's box now reaches 76, plus the clear space.
      { strand: 1, width: 80, rank: 1, spots: [{ x: 16, y: 100 }, { x: 200, y: 100 }], held: 80, fit: levelFit(100) },
    ],
    { width: 390, size: SIZE },
  );
  const b = placed.find((p) => p.strand === 1)!;
  assert.ok(b.x > 80 && b.x < 90, `nudged to ${b.x}`);
  assertNoOverlap(placed);
});

test("a held name whose place is gone takes the free spot nearest it, within a name's width", () => {
  const reqs = (held: number) => [
    {
      strand: 0,
      width: 80,
      rank: 1,
      spots: [{ x: 20, y: 100 }, { x: 150, y: 100 }, { x: 260, y: 100 }],
      held,
      fit: () => NaN,
    },
  ];
  assert.equal(placeNames(reqs(170), { width: 390, size: SIZE })[0].x, 150);
  // Nothing near: the best spot, as for a name not shown before.
  assert.equal(placeNames(reqs(370), { width: 390, size: SIZE })[0].x, 20);
});

test("a name that can stay is not pushed out by a higher-ranked one that has to move", () => {
  const placed = placeNames(
    [
      { strand: 0, width: 80, rank: 1, spots: [{ x: 120, y: 100 }], held: 120, fit: levelFit(100) },
      // Its own place is gone; its next spot is where strand 0 sits.
      { strand: 1, width: 80, rank: 9, spots: [{ x: 120, y: 102 }, { x: 260, y: 102 }], held: 30, fit: () => NaN },
    ],
    { width: 390, size: SIZE },
  );
  assert.deepEqual(
    placed.map((p) => [p.strand, p.x]).sort((a, b) => a[0] - b[0]),
    [
      [0, 120],
      [1, 260],
    ],
  );
});

test("a name panned into one riding the margin queues up behind it, whatever their ranks", () => {
  const placed = placeNames(
    [
      // Riding the left margin (moved past it by the pan), lower priority.
      { strand: 0, width: 42, rank: 1, spots: [{ x: NAME_GUTTER, y: 100 }], held: NAME_GUTTER - 1, fit: levelFit(100, 1e9, 42) },
      // Moving with the pan, now into the rider's clear space.
      { strand: 1, width: 28, rank: 9, spots: [{ x: 200, y: 110 }], held: 69, fit: levelFit(110, 1e9, 28) },
    ],
    { width: 390, size: SIZE },
  );
  const rider = placed.find((p) => p.strand === 0)!;
  const other = placed.find((p) => p.strand === 1)!;
  assert.equal(rider.x, NAME_GUTTER);
  assert.ok(other.x >= 70 && other.x < 71, `queued at ${other.x}`);
});

function assertNoOverlap(names: NameBox[]) {
  for (let i = 0; i < names.length; i++)
    for (let j = i + 1; j < names.length; j++) assert.ok(!overlap(names[i], names[j]), `names ${i} and ${j} overlap`);
}

test("a name box covers the text from cap height to descender", () => {
  const b = nameBox(10, 100, 50, 20);
  assert.deepEqual(b, { x0: 10, y0: 80, x1: 60, y1: 105 });
});

/* ---------------- through the renderer ---------------- */

const NAMES = [
  "Joseph", "Rosa", "Luis", "Carmen", "David", "Ana", "Michael", "Teresa", "Bartholomew", "Maria-Elena",
  "Sam", "Lily", "Christopher", "Gabriela", "Jonathan", "Alexandra", "Ben", "Evangeline", "Theodore", "Mae",
  "Oliver", "Penelope", "Sebastian", "Isabella", "Frederick", "Annabelle", "Wilhelmina", "Jo", "Montgomery", "Clementine",
];
const COLORS = ["#c0503a", "#3f6fb5", "#5f7f52", "#7b5ea7", "#d39a2c", "#c97a9a", "#2f8f8a", "#7a5230"];

/**
 * A family of n: two grandparent couples, their children (paired up), and
 * grandchildren; moments for weddings, births and a few gatherings, with
 * the later-added relatives in none (as when a family grows in the tree
 * before anyone tells a story).
 */
function family(n: number): { people: ThreadPerson[]; moments: ThreadMoment[] } {
  const people: ThreadPerson[] = [];
  const moments: ThreadMoment[] = [];
  for (let i = 0; i < n; i++) {
    const gen = i < 4 ? 0 : i < 12 ? 1 : 2;
    const born = gen === 0 ? 1934 + i * 2 : gen === 1 ? 1958 + (i - 4) * 2 : 1986 + (i - 12) * 2;
    const parents = gen === 0 ? [] : gen === 1 ? (i % 2 ? ["p0", "p2"] : ["p1", "p3"]) : [`p${4 + ((i - 12) % 8)}`];
    const spouse = gen === 0 ? [`p${i ^ 2}`] : [];
    people.push({ id: `p${i}`, name: NAMES[i % NAMES.length], born, parentIds: parents, spouseIds: spouse, color: COLORS[i % COLORS.length] });
  }
  const add = (title: string, year: number, ids: number[]) => {
    const ps = ids.filter((i) => i < n).map((i) => `p${i}`);
    if (ps.length) moments.push({ id: `m${moments.length}`, title, year, participantIds: ps });
  };
  add("The wedding", 1958, [0, 2]);
  add("The dance at the hall", 1960, [1, 3]);
  add("First steps", 1960, [0, 2, 4]);
  add("The lake", 1963, [1, 3, 5]);
  add("The green sedan", 1968, [4, 5, 6]);
  add("Sunday tamales", 1972, [1, 3, 5, 7]);
  add("Graduation", 1980, [4, 6]);
  add("The move west", 1985, [4, 5, 8, 9]);
  add("A new baby", 1988, [4, 12]);
  add("The first bike", 1994, [12, 13]);
  add("Grandpa's birthday", 2004, [0, 4, 12, 13, 14]);
  add("Reunion", 2018, [4, 5, 12, 13, 14, 15, 16]);
  add("The new house", 2021, [10, 11, 17]);
  return { people, moments };
}

/** A 2D context that draws nothing and measures handwriting at ~0.5em a letter. */
function stubContext(): CanvasRenderingContext2D {
  const state: Record<string | symbol, unknown> = { font: "16px serif" };
  const noop = () => undefined;
  const px = () => Number(/(\d+(?:\.\d+)?)px/.exec(String(state.font))?.[1] ?? 16);
  return new Proxy(state, {
    get(t, k) {
      if (k === "measureText") return (s: string) => ({ width: s.length * px() * 0.5 });
      if (k === "createRadialGradient" || k === "createLinearGradient") return () => ({ addColorStop: noop });
      return k in t ? t[k] : noop;
    },
    set(t, k, v) {
      t[k] = v;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

const SIZES: [number, number][] = [
  [390, 844],
  [1440, 900],
];

/** The views people actually see: whole family, phone opening zoom, decades, single years at both ends and the middle. */
function views(model: ReturnType<typeof buildModel>, W: number) {
  const b = { tMin: model.tMin, tMax: model.tMax, width: W };
  const knots = model.knots.map((k) => k.t);
  return [
    ["whole", initialCamera(1, null, b, knots)],
    ["open", initialCamera(null, null, b, knots)],
    ["decade", initialCamera("decade", null, b, knots)],
    ["year", initialCamera("year", null, b, knots)],
    ["year, today", initialCamera("year", NOW, b, knots)],
    ["year, 1990", initialCamera("year", 1990.5, b, knots)],
    ["x4, today", initialCamera(4, NOW, b, knots)],
    ["x2, 1975", initialCamera(2, 1975, b, knots)],
  ] as const;
}

function frame(model: ReturnType<typeof buildModel>, W: number, H: number, cam: { cx: number; s: number }, highlight = -1) {
  const n = model.strands.length;
  const layout = makeLayout(W, H, n);
  const nameShown = new Uint8Array(n);
  const st = {
    cam,
    layout,
    dim: model.strands.map((_, i) => (highlight >= 0 && i !== highlight ? 1 : 0)),
    lift: model.strands.map((_, i) => (i === highlight ? 1 : 0)),
    hoverKnot: -1,
    swell: model.knots.map(() => 0),
    reveal: model.tNow + 2,
    pop: model.knots.map(() => 1),
    fonts: { hand: "hand", sans: "sans", serif: "serif" },
    highlight,
    nameShown,
  };
  const ctx = stubContext();
  // Twice, as the engine does across frames: the second uses the first's history.
  drawFrame(ctx, model, st);
  return { geom: drawFrame(ctx, model, st), layout };
}

function assertClean(names: NameBox[], W: number, what: string) {
  for (const b of names) {
    assert.ok(insideGutter(b, W), `${what}: name at ${b.x0.toFixed(1)}..${b.x1.toFixed(1)} leaves the ${NAME_GUTTER}px gutter`);
  }
  for (let i = 0; i < names.length; i++) {
    for (let j = i + 1; j < names.length; j++) {
      assert.ok(!overlap(names[i], names[j]), `${what}: names ${i} and ${j} overlap`);
    }
  }
}

for (const n of [9, 18, 24, 30]) {
  for (const [W, H] of SIZES) {
    test(`${n} people at ${W}px: no two names overlap and none leaves the gutter`, () => {
      const { people, moments } = family(n);
      const model = buildModel(people, moments, NOW);
      let total = 0;
      for (const [label, cam] of views(model, W)) {
        const { geom, layout } = frame(model, W, H, cam);
        assertClean(geom.names, W, `${n} people, ${W}px, ${label}`);
        assert.ok(nameFontPx(layout) >= NAME_MIN_PX, "names never shrink below legible");
        // Each name is written by its own thread: at most leading in to where it
        // begins, never past its end.
        for (const b of geom.names as (NameBox & { strand: number })[]) {
          const pts = geom.strands[b.strand];
          const end = pts[(geom.strandLens[b.strand] - 1) * 2];
          const who = `${n} people, ${W}px, ${label}: ${model.strands[b.strand].person.name}`;
          assert.ok(b.x1 >= pts[0] - 14, `${who} is wholly before its thread`);
          assert.ok(b.x1 <= end + 0.5, `${who} runs past its thread's end`);
        }
        total += geom.names.length;
      }
      // Crowding drops some names, never all of them.
      assert.ok(total > 0, "some names are written");
    });

    test(`${n} people at ${W}px: the followed person's name is always written`, () => {
      const { people, moments } = family(n);
      const model = buildModel(people, moments, NOW);
      for (const [label, cam] of views(model, W)) {
        for (const hl of [0, Math.floor(n / 2), n - 1]) {
          const { geom } = frame(model, W, H, cam, hl);
          assertClean(geom.names, W, `${n} people, ${W}px, ${label}, following ${hl}`);
          const pts = geom.strands[hl];
          const len = geom.strandLens[hl];
          // On screen: some of the thread lies inside the gutters.
          let visible = false;
          for (let k = 0; k < len && !visible; k++) visible = pts[k * 2] >= NAME_GUTTER && pts[k * 2] <= W - NAME_GUTTER;
          if (!visible) continue;
          const name = geom.names.find((b) => (b as { strand?: number }).strand === hl);
          assert.ok(name, `${n} people, ${W}px, ${label}: following ${hl}, its name is missing`);
        }
      }
    });
  }
}

test("tight lanes write names on their threads; roomy ones above", () => {
  assert.equal(namesInline(makeLayout(390, 844, 9)), false);
  assert.equal(namesInline(makeLayout(390, 844, 18)), false);
  assert.equal(namesInline(makeLayout(390, 844, 24)), true);
  assert.equal(namesInline(makeLayout(390, 844, 30)), true);
  assert.equal(namesInline(makeLayout(1440, 900, 24)), false);
  assert.equal(namesInline(makeLayout(1440, 900, 30)), true);
});

for (const [n, W, H] of [
  [24, 390, 844],
  [30, 390, 844],
  [30, 1440, 900],
] as const) {
  test(`${n} people at ${W}px: crowding still names most threads`, () => {
    const { people, moments } = family(n);
    const model = buildModel(people, moments, NOW);
    for (const [label, cam] of views(model, W)) {
      const { geom } = frame(model, W, H, cam);
      let onScreen = 0;
      for (let i = 0; i < geom.strands.length; i++) {
        const pts = geom.strands[i];
        const len = geom.strandLens[i];
        let vis = 0;
        for (let k = 0; k < len; k++) if (pts[k * 2] >= NAME_GUTTER && pts[k * 2] <= W - NAME_GUTTER) vis++;
        if (vis * 2.5 > 120) onScreen++; // more than a name's width of thread in view
      }
      assert.ok(geom.names.length >= onScreen * 0.5, `${label}: ${geom.names.length} names for ${onScreen} threads in view`);
    }
  });
}

test("9 people on a laptop, zoomed out: everyone's name is written", () => {
  const { people, moments } = family(9);
  const model = buildModel(people, moments, NOW);
  const b = { tMin: model.tMin, tMax: model.tMax, width: 1440 };
  const { geom } = frame(model, 1440, 900, initialCamera(1, null, b, []));
  assert.equal(geom.names.length, 9);
});

/* ---------------- while panning ---------------- */

type Placed = NameBox & { strand: number; x: number; y: number };

/**
 * A slow drag: the view panned 1px a frame (content moving left), through
 * the real renderer with the engine's per-strand memory. Each frame's names.
 */
function pan(n: number, W: number, H: number, skip: number, frames: number): Map<number, Placed>[] {
  const { people, moments } = family(n);
  const model = buildModel(people, moments, NOW);
  const b = { tMin: model.tMin, tMax: model.tMax, width: W };
  const s = initialCamera("year", null, b, model.knots.map((k) => k.t)).s;
  let cam = clampCam({ s, cx: -1e9 }, b);
  cam = { s, cx: cam.cx + skip / s };
  const layout = makeLayout(W, H, model.strands.length);
  const st = {
    cam,
    layout,
    dim: model.strands.map(() => 0),
    lift: model.strands.map(() => 0),
    hoverKnot: -1,
    swell: model.knots.map(() => 0),
    reveal: model.tNow + 2,
    pop: model.knots.map(() => 1),
    fonts: { hand: "hand", sans: "sans", serif: "serif" },
    highlight: -1,
    nameShown: new Uint8Array(model.strands.length),
    nameAt: new Float64Array(model.strands.length).fill(NaN),
  };
  const ctx = stubContext();
  const out: Map<number, Placed>[] = [];
  for (let f = 0; f < frames; f++) {
    st.cam = { s, cx: cam.cx + f / s };
    const names = drawFrame(ctx, model, st).names as Placed[];
    out.push(new Map(names.map((p) => [p.strand, p])));
  }
  return out;
}

// Stretches of the pan (frames from the far left) where names used to hop most.
for (const [n, W, H, skip, FRAMES] of [
  [30, 390, 844, 3500, 1500],
  [24, 390, 844, 6000, 1000],
  [30, 1440, 900, 4000, 800],
] as const) {
  test(`${n} people at ${W}px, panned 1px a frame: names stay put on their threads, not hopping between spots`, () => {
    const seq = pan(n, W, H, skip, FRAMES);
    let moves = 0;
    let shown = 0;
    // Per strand: the frame it last moved, and where (in pan-free px) it moved from.
    const left = new Map<number, { f: number; x: number; y: number }>();
    const flips = new Map<number, number>();
    for (let f = 1; f < FRAMES; f++) {
      // Shown or not: a name does not blink on and off.
      for (let i = 0; i < n; i++) if (seq[f].has(i) !== seq[f - 1].has(i)) flips.set(i, (flips.get(i) ?? 0) + 1);
      for (const [i, c] of seq[f]) {
        shown++;
        const a = seq[f - 1].get(i);
        if (!a) continue;
        // Held in place it moves exactly with the pan (-1px), riding a margin
        // or behind a rider it stays still: anything else is a move.
        const dx = c.x - a.x;
        if (Math.abs(dx + 1) <= 3 && Math.abs(c.y - a.y) <= 4) continue;
        moves++;
        // Never back to the spot it just left.
        const prev = left.get(i);
        if (prev && f - prev.f <= 30) {
          const back = Math.abs(c.x + f - prev.x) < 3 && Math.abs(c.y - prev.y) < 3;
          assert.ok(!back, `strand ${i} went back to the spot it left at frame ${prev.f} (${f})`);
        }
        left.set(i, { f, x: a.x + f - 1, y: a.y });
      }
    }
    // A name moves only when its spot is taken or gone: rarely. (Before names
    // kept their places, about 20 in every 1000 name-frames on a phone hopped.)
    assert.ok(moves * 1000 <= shown * 5, `${moves} moves in ${shown} name-frames`);
    for (const [i, k] of flips) assert.ok(k <= 6, `strand ${i} shown and hidden ${k} times`);
  });
}
