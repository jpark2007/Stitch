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
}

/** Everything drawn on any canvas since the last reset. */
const drawn = { texts: [] as Print[], rects: [] as Rect[] };

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
      drawn.rects.push({ fill: state.fillStyle, w, h });
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
  constructor(..._: unknown[]) {
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

/** Lay a story page out as drawStoryPage does (title on one line). */
function layout(text: string, photos: number, tail = 34) {
  return art.layoutStory(measureCtx(), text, fonts, {
    width: 770,
    top: 262 + 72 + 8 + 58,
    tail,
    photos: Array.from({ length: Math.min(2, photos) }, (_, i) => ASPECTS[i % 3]),
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
          const room = art.photoRoom(l.photos, bottom(l));
          assert.ok(room >= art.PHOTO_MIN_ROOM, `photo room ${room}`);
          // The widths drawStoryPage hands drawPhoto.
          for (let i = 0; i < l.photos; i++) {
            const w = l.photos === 1 ? Math.min(520, room * ASPECTS[0] * 0.9) : Math.min(i ? 330 : 430, room * ASPECTS[i] * (i ? 0.72 : 0.82));
            assert.ok(w > 0 && Number.isFinite(w), `photo ${i} width ${w}`);
          }
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
