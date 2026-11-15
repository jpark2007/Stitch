/**
 * Story pages of any length: the words fit (smaller, then cut with "…"),
 * a photo is only taped in with room for it, and nothing throws.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The bug (Jonah, Issue #1): a story of ~800 characters ran past the page,
 * the photos' room went negative, drawPhoto was handed a negative width,
 * painting its stand-in scene threw, and the book never got past "opening
 * the book".
 *
 * The pages are drawn on a stand-in 2D canvas that measures text at half an
 * em a character and fails where a browser's does: a gradient with a
 * negative radius, image data with no pixels, drawing a canvas with no
 * pixels. pageArt.ts imports by extensionless and "@/" paths as the app
 * does; a resolve hook maps those for node (as in personBook.test.ts).
 */

import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import { describe, it } from "node:test";
import type { BookPhoto, BookSpread } from "./types";

type ResolveNext = (specifier: string, context: unknown) => unknown;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: unknown, next: ResolveNext): unknown }): unknown;
};
const webRoot = new URL("../../", import.meta.url);
registerHooks({
  resolve(specifier, context, next) {
    const spec = specifier.startsWith("@/") ? new URL(specifier.slice(2), webRoot).href : specifier;
    try {
      return next(spec, context);
    } catch (e) {
      if (/^(\.|file:)/.test(spec) && !/\.[cm]?[jt]sx?$/.test(spec)) return next(`${spec}.ts`, context);
      throw e;
    }
  },
});

/* ------------------------------------------------------------------------ */
/* A stand-in canvas                                                         */
/* ------------------------------------------------------------------------ */

interface Print {
  text: string;
  font: string;
  x: number;
  y: number;
}
interface Rect {
  fill: unknown;
  w: number;
  h: number;
  /** The last translate before it: a print's centre (photoArt.printPhoto translates there, then fills). */
  cx: number;
  cy: number;
}

/** Everything drawn on any canvas since the last reset. */
const drawn = { texts: [] as Print[], rects: [] as Rect[], at: { x: 0, y: 0 } };

const noop = () => undefined;
const gradient = () => ({ addColorStop: noop });

class FakeCanvas {
  #w = 300;
  #h = 150;
  get width() {
    return this.#w;
  }
  set width(v: number) {
    this.#w = Math.max(0, Math.floor(Number(v) || 0));
  }
  get height() {
    return this.#h;
  }
  set height(v: number) {
    this.#h = Math.max(0, Math.floor(Number(v) || 0));
  }
  getContext() {
    return fakeContext(this);
  }
}

function fakeContext(canvas: FakeCanvas) {
  const state: Record<string | symbol, unknown> = {
    canvas,
    font: "10px sans-serif",
    fillStyle: "#000",
    measureText(text: string) {
      const size = Number(/([\d.]+)px/.exec(String(state.font))?.[1] ?? 10);
      return { width: text.length * size * 0.5, actualBoundingBoxRight: size * 0.5 };
    },
    fillText(text: string, x: number, y: number) {
      drawn.texts.push({ text, font: String(state.font), x, y });
    },
    fillRect(_x: number, _y: number, w: number, h: number) {
      drawn.rects.push({ fill: state.fillStyle, w, h, cx: drawn.at.x, cy: drawn.at.y });
    },
    translate(x: number, y: number) {
      drawn.at = { x, y };
    },
    createLinearGradient: gradient,
    createRadialGradient(_x0: number, _y0: number, r0: number, _x1: number, _y1: number, r1: number) {
      if (r0 < 0 || r1 < 0) throw new RangeError("IndexSizeError: negative radius");
      return gradient();
    },
    createPattern: () => null,
    createImageData(w: number, h: number) {
      if (!(w > 0 && h > 0)) throw new RangeError("IndexSizeError: no pixels");
      return { data: new Uint8ClampedArray(w * h * 4), width: w, height: h };
    },
    drawImage(image: unknown) {
      if (image instanceof FakeCanvas && (image.width === 0 || image.height === 0)) {
        throw new Error("InvalidStateError: the canvas has no pixels");
      }
    },
  };
  return new Proxy(state, {
    get: (t, k) => (k in t ? t[k] : noop),
    set: (t, k, v) => {
      t[k] = v;
      return true;
    },
  });
}

class FakePath2D {
  constructor() {
    return new Proxy(this, { get: (t, k) => (k in t ? (t as Record<string | symbol, unknown>)[k] : noop) });
  }
}

Object.assign(globalThis, {
  document: { createElement: () => new FakeCanvas(), documentElement: {}, fonts: { load: async () => [] } },
  Path2D: FakePath2D,
  HTMLImageElement: class {},
  Image: class {},
});

type PageArt = typeof import("./pageArt");
type PhotoArt = typeof import("./photoArt");
const paths = ["./pageArt.ts", "./photoArt.ts"];
const art: PageArt = await import(paths[0]);
const photoArt: PhotoArt = await import(paths[1]);

const fonts = { display: "serif", hand: "cursive", sans: "sans-serif" };
const PHOTO_FILL = "#fbf8f1";

/* ------------------------------------------------------------------------ */
/* Stories                                                                   */
/* ------------------------------------------------------------------------ */

const SENTENCE = "It was raining on the church steps, and Ana was carrying far too many hymn books. ";
const story = (n: number) => SENTENCE.repeat(Math.ceil(n / SENTENCE.length) + 1).slice(0, n).trim();
const LENGTHS = [0, 220, 800, 3000, 10000];
const ASPECTS = [4 / 3, 1, 3 / 4];
const photo = (i: number): BookPhoto => ({ id: `p${i}`, scene: "hills", caption: "the drive home", aspect: ASPECTS[i % 3] });

function spread(text: string, photos: number, extra: Partial<BookSpread> = {}): BookSpread {
  return {
    id: "m",
    title: "How I met your mother",
    year: 1987,
    tellers: ["David", "Ana"],
    text,
    photos: Array.from({ length: photos }, (_, i) => photo(i)),
    ...extra,
  };
}

function measureCtx() {
  return document.createElement("canvas").getContext("2d") as CanvasRenderingContext2D;
}

/** Lay a story page out as drawStoryPage does (title on one line, captioned photos as photo()). */
function layout(text: string, photos: number, tail = 34) {
  const n = Math.min(2, photos);
  return art.layoutStory(measureCtx(), text, fonts, {
    width: 770,
    top: 262 + 72 + 8 + 58,
    tail,
    photos: Array.from({ length: n }, (_, i) => ASPECTS[i % 3]),
    captions: Array.from({ length: n }, () => true),
  });
}

/** The words' bottom on the page, as drawStoryPage reaches it. */
const bottom = (l: ReturnType<typeof layout>, tail = 34) => 262 + 72 + 8 + 58 + l.body.height + tail;

describe("layoutStory", () => {
  for (const n of LENGTHS) {
    for (let photos = 0; photos <= 3; photos++) {
      it(`${n} characters, ${photos} photo${photos === 1 ? "" : "s"}: fits, every photo kept has room`, () => {
        const l = layout(story(n), photos);
        assert.ok(l.photos <= Math.min(2, photos));
        assert.ok(l.body.size >= 24, `body ${l.body.size}px is under the readable floor`);
        if (l.photos > 0) {
          // The widths drawStoryPage hands drawPhoto.
          const widths = art.photoWidths(art.photoRoom(l.photos, bottom(l)), ASPECTS.slice(0, l.photos));
          assert.equal(widths.length, l.photos);
          for (const w of widths) assert.ok(Number.isFinite(w) && w >= art.PHOTO_MIN_W, `photo width ${w}`);
        } else {
          assert.ok(bottom(l) <= 1300 - 150, `words run to ${bottom(l)}`);
        }
      });
    }
  }

  it("keeps a normal story as it was: the usual size, every photo", () => {
    for (const photos of [0, 1, 2]) {
      const l = layout(story(220), photos);
      assert.equal(l.body.size, 31);
      assert.equal(l.body.lead, 46);
      assert.equal(l.photos, photos);
      assert.equal(l.body.clamped, false);
      assert.equal(l.body.lines.join(" "), story(220).slice(1).trim());
    }
    const short = layout("A day at the sea.", 1);
    assert.equal(short.body.size, 37);
    assert.equal(short.photos, 1);
    // The demo's first page: two tellings' labels above two photos.
    const tail = art.storyTailHeight({ tellings: [{ id: "a", name: "Ana", color: "#b2473a" }, { id: "b", name: "David", color: "#34465c" }] });
    const told = layout(story(230), 2, tail);
    assert.equal(told.body.size, 31);
    assert.equal(told.photos, 2);
  });

  it("leaves out a photo too narrow to print", () => {
    assert.deepEqual(art.photoWidths(-40, [4 / 3]).map((w) => w > 0), [false]);
    // Three tellings' labels leave the photos no room: they are left out, the words keep their size.
    const tellings = ["a", "b", "c"].map((id) => ({ id, name: id, color: "#b2473a" }));
    const l = layout(story(230), 2, art.storyTailHeight({ tellings }));
    assert.equal(l.photos, 0);
    assert.equal(l.body.size, 31);
  });

  it("steps the size down before leaving photos out", () => {
    const l = layout(story(800), 2);
    assert.ok(l.body.size < 31, `still ${l.body.size}px`);
    assert.equal(l.body.clamped, false);
  });

  it("cuts a story too long for any size with an ellipsis, at the floor size", () => {
    for (const n of [3000, 10000]) {
      const l = layout(story(n), 2);
      assert.equal(l.photos, 0);
      assert.equal(l.body.size, 24);
      assert.equal(l.body.clamped, true);
      assert.match(l.body.lines[l.body.lines.length - 1], /\w…$/);
    }
  });

  it("is quick for a very long story", () => {
    const t0 = performance.now();
    layout(story(100000), 2);
    assert.ok(performance.now() - t0 < 500);
  });
});

/* ------------------------------------------------------------------------ */
/* Drawing                                                                   */
/* ------------------------------------------------------------------------ */

const reset = () => {
  drawn.texts.length = 0;
  drawn.rects.length = 0;
};
const prints = () => drawn.rects.filter((r) => r.fill === PHOTO_FILL);

describe("drawStoryPage", () => {
  for (const n of LENGTHS) {
    for (let photos = 0; photos <= 3; photos++) {
      it(`draws ${n} characters with ${photos} photo${photos === 1 ? "" : "s"}`, async () => {
        reset();
        const page = await art.drawStoryPage(spread(story(n), photos), 2, fonts, 30);
        assert.ok(page.canvas);
        for (const p of prints()) assert.ok(p.w > 0 && p.h > 0, `a print ${p.w}×${p.h}`);
        assert.ok(prints().length <= Math.min(2, photos));
        // Every line of words stays on the page, above its number.
        const words = drawn.texts.filter((t) => /px serif$/.test(t.font) && !/^italic/.test(t.font));
        for (const t of words) assert.ok(t.y <= 1300 - 140, `"${t.text.slice(0, 20)}" at y ${t.y}`);
      });
    }
  }

  it("draws a normal page's photos", async () => {
    reset();
    await art.drawStoryPage(spread(story(220), 2), 2, fonts, 30);
    assert.equal(prints().length, 2);
  });

  it("draws with tellings' labels and a long title", async () => {
    reset();
    const tellings = ["a", "b", "c"].map((id) => ({ id, name: id, color: "#b2473a", text: story(5000) }));
    const title = "A very long title that goes on and on about the summer the whole family drove to the lake ".repeat(3);
    await art.drawStoryPage(spread("", 2, { tellings, title }), 2, fonts, 30, "b");
    const titleLines = drawn.texts.filter((t) => t.font.startsWith("600 64px"));
    assert.ok(titleLines.length <= 3);
    assert.match(titleLines[titleLines.length - 1].text, /…$/);
  });

  it("draws the closing page with a long line", async () => {
    await art.drawStoryPage(spread(story(3000), 0, { kind: "end", title: "The end" }), 8, fonts, 30);
  });
});

describe("drawPhoto", () => {
  const ctx = () => measureCtx();
  for (const width of [-200, -1, 0, 3, Number.NaN, Number.POSITIVE_INFINITY]) {
    it(`leaves out a print ${width} wide, without throwing`, async () => {
      reset();
      const y = await photoArt.drawPhoto(ctx(), photo(0), 10, 700, width, 0.03, 1, fonts, "top");
      assert.equal(y, 700);
      assert.equal(prints().length, 0);
    });
  }

  for (const aspect of [0, -1, Number.NaN, 1e-6, 1e6]) {
    it(`draws a print with aspect ${aspect} at a sane size`, async () => {
      reset();
      const y = await photoArt.drawPhoto(ctx(), { ...photo(0), aspect }, 10, 700, 300, 0.03, 1, fonts, "corners");
      assert.ok(Number.isFinite(y) && y > 700 && y < 700 + 300 * 5);
    });
  }

  it("paints a stand-in scene of any size", () => {
    for (const [w, h] of [[-10, 20], [0, 0], [Number.NaN, 5], [1e9, 1e9]]) {
      const c = photoArt.paintScene("sea", w, h);
      assert.ok(c.width >= 1 && c.height >= 1 && c.width <= 4096 && c.height <= 4096);
    }
  });
});

/* ------------------------------------------------------------------------ */
/* Photo sizing                                                              */
/* ------------------------------------------------------------------------ */

// Jonah (Issue #1): uploaded photos printed too small. One photo should be
// large and fill the space under the story; two shrink to share it.

const LEFT = 96;
const RIGHT = 1000 - 134;
const SHORT = "A day at the sea.";
/** The lowest a print may reach: one photo, or two (photoRoom). */
const FOOT = { 1: 1300 - 150, 2: 1300 - 140 } as const;
const shot = (aspect: number, caption?: string): BookPhoto => ({ id: `s${aspect}`, scene: "sea", aspect, ...(caption ? { caption } : {}) });

/** Draw a story page and return its prints' boxes (unrotated), in the order taped. */
async function drawPrints(photos: BookPhoto[], text = SHORT, extra: Partial<BookSpread> = {}) {
  reset();
  await art.drawStoryPage(spread(text, 0, { ...extra, photos }), 2, fonts, 30);
  // The words and tellers, not the page number at the foot.
  const words = drawn.texts.filter((t) => t.y < 1200 && !/^italic/.test(t.font));
  const wordsEnd = Math.max(...words.map((t) => t.y));
  const boxes = prints().map((p) => ({ w: p.w, h: p.h, x0: p.cx - p.w / 2, x1: p.cx + p.w / 2, y0: p.cy - p.h / 2, y1: p.cy + p.h / 2 }));
  return { boxes, wordsEnd };
}

describe("photo sizing", () => {
  it("printHeight is the bottom drawPhoto draws to", async () => {
    for (const width of [120, 333, 365, 501, 770]) {
      for (const aspect of [4, 16 / 9, 4 / 3, 1, 3 / 4, 0.25]) {
        for (const caption of [undefined, "the drive home"]) {
          const y = await photoArt.drawPhoto(measureCtx(), shot(aspect, caption), 10, 700, width, 0.03, 1, fonts, "top");
          assert.equal(y - 700, art.printHeight(width, aspect, Boolean(caption)), `${width} wide at ${aspect}${caption ? ", captioned" : ""}`);
        }
      }
    }
  });

  it("prints one landscape photo nearly the measure wide, filling the space", async () => {
    const { boxes, wordsEnd } = await drawPrints([shot(4 / 3)]);
    assert.equal(boxes.length, 1);
    const [p] = boxes;
    assert.ok(p.w >= 700, `only ${p.w} wide (it was at most 520)`);
    assert.ok(p.x0 >= LEFT && p.x1 <= RIGHT, `runs ${p.x0}–${p.x1}`);
    assert.ok(p.y0 > wordsEnd + 30, `top ${p.y0} on the words (${wordsEnd})`);
    assert.ok(p.y1 <= FOOT[1], `bottom ${p.y1} past the foot`);
    // Either the whole measure wide, or the whole space tall.
    assert.ok(p.w === art.PHOTO_MEASURE || FOOT[1] - p.y1 <= 3, `${p.w} wide, ${FOOT[1] - p.y1} short of the foot`);
  });

  it("prints one portrait photo the space's full height", async () => {
    const { boxes, wordsEnd } = await drawPrints([shot(3 / 4)]);
    const [p] = boxes;
    assert.ok(p.w >= 400 && p.w < art.PHOTO_MEASURE, `${p.w} wide (it was at most ~300)`);
    assert.ok(p.y0 > wordsEnd + 30 && p.y1 <= FOOT[1] && FOOT[1] - p.y1 <= 3, `runs ${p.y0}–${p.y1}`);
    // Centred on the measure, give or take its lean to the right.
    assert.ok(Math.abs((p.x0 + p.x1) / 2 - (LEFT + RIGHT) / 2) <= 10);
  });

  it("starts the photos higher when the words end sooner", () => {
    assert.equal(art.photoTop(420), 560);
    assert.ok(art.photoRoom(1, 420) > 1300 - 150 - 700, "still floored at 700");
    assert.equal(art.photoTop(800), 846);
  });

  it("shares the measure between two photos side by side", async () => {
    const one = (await drawPrints([shot(4 / 3)])).boxes[0];
    const { boxes, wordsEnd } = await drawPrints([shot(4 / 3), shot(4 / 3)]);
    assert.equal(boxes.length, 2);
    const [a, b] = boxes;
    // Two landscapes: each takes half the measure (less the gap).
    assert.equal(a.w, b.w);
    assert.ok(a.w >= 340 && a.w < one.w, `${a.w} wide`);
    assert.ok(a.x1 + 40 <= b.x0, `the prints overlap: ${a.x1} and ${b.x0}`);
    assert.ok(a.x0 >= LEFT - 6 && b.x1 <= RIGHT + 8);
    for (const p of boxes) assert.ok(p.y0 > wordsEnd + 30 && p.y1 <= FOOT[2], `runs ${p.y0}–${p.y1}`);
    assert.ok(b.y0 > a.y0, "the second is set lower");

    // A landscape and a portrait: the portrait, as wide, is the taller; the
    // pair is set in the middle of the space.
    const mixed = (await drawPrints([shot(4 / 3), shot(3 / 4)])).boxes;
    assert.equal(mixed.length, 2);
    assert.equal(mixed[0].w, mixed[1].w);
    assert.ok(mixed[1].h > mixed[0].h * 1.5);
    assert.ok(mixed[1].y1 <= FOOT[2], `portrait ends at ${mixed[1].y1}`);
    assert.ok(mixed[0].x1 + 40 <= mixed[1].x0);
    const above = mixed[0].y0 - (wordsEnd + 46);
    const below = FOOT[2] - mixed[1].y1;
    assert.ok(Math.abs(above - below) <= 30, `${above} above, ${below} below`);

    // Two portraits under a long telling: each as tall as the space allows.
    const tall = (await drawPrints([shot(3 / 4), shot(3 / 4)], story(400))).boxes;
    assert.equal(tall.length, 2);
    assert.ok(tall[0].w < 365 && FOOT[2] - Math.max(tall[0].y1, tall[1].y1) <= 3, `${tall[0].w} wide, to ${tall[1].y1}`);
  });

  it("makes room for a caption under the print", async () => {
    const plain = (await drawPrints([shot(3 / 4)])).boxes[0];
    const { boxes } = await drawPrints([shot(3 / 4, "the drive home")]);
    const [p] = boxes;
    assert.ok(p.w < plain.w, "a captioned portrait is narrower, to keep its caption on the page");
    assert.ok(p.y1 <= FOOT[1] && FOOT[1] - p.y1 <= 3, `ends at ${p.y1}`);
    // Sized as if uncaptioned, its caption would run past the foot.
    const room = art.photoRoom(1, 600);
    const [w] = art.photoWidths(room, [3 / 4]);
    assert.ok(art.printHeight(w, 3 / 4, true) > room);
    const [wc] = art.photoWidths(room, [3 / 4], [true]);
    assert.ok(art.printHeight(wc, 3 / 4, true) <= room);
  });

  it("never sizes a print past its room, nor a print in no room", () => {
    const sets: [number[], boolean[]][] = [
      [[4 / 3], [false]],
      [[3 / 4], [true]],
      [[4], [true]],
      [[0.25], [false]],
      [[4 / 3, 3 / 4], [true, false]],
      [[0.25, 4], [false, true]],
    ];
    for (let y = 400; y <= 1300; y += 7) {
      for (const [aspects, captions] of sets) {
        const room = art.photoRoom(aspects.length, y);
        const widths = art.photoWidths(room, aspects, captions);
        widths.forEach((w, i) => {
          const fits = i === 0 ? room : room - art.pairDrop(room);
          if (room <= 0) assert.ok(!(w > 0), `a ${w}-wide print in room ${room}`);
          else if (w > 0) assert.ok(art.printHeight(w, aspects[i], captions[i]) <= fits, `y ${y}: ${w} wide in ${fits}`);
          assert.ok(!(w > (aspects.length === 1 ? art.PHOTO_MEASURE : art.PHOTO_MEASURE / 2)));
        });
      }
    }
  });

  it("keeps the prints under long words, or leaves them out", async () => {
    for (const n of [300, 500, 700, 900, 1200]) {
      for (const photos of [[shot(4 / 3, "the drive home")], [shot(3 / 4), shot(4 / 3, "the lake")]]) {
        const { boxes, wordsEnd } = await drawPrints(photos, story(n));
        for (const p of boxes) {
          assert.ok(p.w >= art.PHOTO_MIN_W, `${n} characters: a ${p.w}-wide print`);
          assert.ok(p.y0 > wordsEnd + 30, `${n} characters: a print at ${p.y0} on the words (${wordsEnd})`);
          assert.ok(p.y1 <= FOOT[boxes.length as 1 | 2], `${n} characters: a print to ${p.y1}`);
        }
      }
    }
  });

  it("draws no print without photos", async () => {
    assert.deepEqual(art.photoWidths(500, []), []);
    const { boxes } = await drawPrints([]);
    assert.equal(boxes.length, 0);
  });
});
