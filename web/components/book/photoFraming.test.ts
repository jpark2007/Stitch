/**
 * Uploaded photos keep their own shape: a portrait is printed as a portrait,
 * cropped toward its upper third (faces), a landscape cut about the middle,
 * and the story page places one, the other or a pair without running into
 * the words or off the page.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The bug: a 3024x4032 phone portrait was cover-fit into a fixed 4:3 frame,
 * cut about the middle, and only the subject's chin showed.
 *
 * Images load in a stand-in: an Image whose src "img:WxH" loads as a W x H
 * picture. The page is drawn on a stand-in canvas (as in pageArt.test.ts)
 * that records each print's centre and size, and each image drawn.
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
/* Stand-ins: canvas and images                                              */
/* ------------------------------------------------------------------------ */

const PHOTO_FILL = "#fbf8f1";
interface Print {
  cx: number;
  cy: number;
  w: number;
  h: number;
}
interface Drawn {
  image: unknown;
  args: number[];
}
const drawn = { prints: [] as Print[], images: [] as Drawn[], texts: [] as { text: string; font: string; y: number }[] };
const reset = () => {
  drawn.prints.length = 0;
  drawn.images.length = 0;
  drawn.texts.length = 0;
};

const noop = () => undefined;
const gradient = () => ({ addColorStop: noop });

class FakeCanvas {
  width = 300;
  height = 150;
  getContext() {
    return fakeContext(this);
  }
}

function fakeContext(canvas: FakeCanvas) {
  let at = [0, 0];
  const state: Record<string | symbol, unknown> = {
    canvas,
    font: "10px sans-serif",
    fillStyle: "#000",
    measureText(text: string) {
      const size = Number(/([\d.]+)px/.exec(String(state.font))?.[1] ?? 10);
      return { width: text.length * size * 0.5, actualBoundingBoxRight: size * 0.5 };
    },
    translate(x: number, y: number) {
      at = [x, y];
    },
    fillText(text: string, _x: number, y: number) {
      drawn.texts.push({ text, font: String(state.font), y });
    },
    fillRect(_x: number, _y: number, w: number, h: number) {
      if (state.fillStyle === PHOTO_FILL) drawn.prints.push({ cx: at[0], cy: at[1], w, h });
    },
    createLinearGradient: gradient,
    createRadialGradient: gradient,
    createPattern: () => null,
    createImageData(w: number, h: number) {
      return { data: new Uint8ClampedArray(Math.max(1, w * h * 4)), width: w, height: h };
    },
    drawImage(image: unknown, ...args: number[]) {
      drawn.images.push({ image, args });
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

/** An image that loads "img:WxH" as a W x H picture, and fails anything else. */
class FakeImage {
  width = 0;
  height = 0;
  naturalWidth = 0;
  naturalHeight = 0;
  crossOrigin = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  set src(v: string) {
    const m = /^img:(\d+)x(\d+)/.exec(v);
    setTimeout(() => {
      if (!m) return this.onerror?.();
      this.width = this.naturalWidth = Number(m[1]);
      this.height = this.naturalHeight = Number(m[2]);
      this.onload?.();
    }, 0);
  }
}

Object.assign(globalThis, {
  document: { createElement: () => new FakeCanvas(), documentElement: {}, fonts: { load: async () => [] } },
  Path2D: FakePath2D,
  HTMLImageElement: FakeImage,
  Image: FakeImage,
});

type PageArt = typeof import("./pageArt");
type PhotoArt = typeof import("./photoArt");
const paths = ["./pageArt.ts", "./photoArt.ts"];
const art: PageArt = await import(paths[0]);
const photo: PhotoArt = await import(paths[1]);

const fonts = { display: "serif", hand: "cursive", sans: "sans-serif" };

/* ------------------------------------------------------------------------ */
/* Framing math                                                              */
/* ------------------------------------------------------------------------ */

const near = (a: number | null, b: number, msg?: string) => assert.ok(a !== null && Math.abs(a - b) < 1e-9, msg ?? `${a} is not ${b}`);

describe("printAspect", () => {
  it("prints a phone portrait as a portrait", () => {
    near(photo.printAspect(3024, 4032), 3 / 4);
    // Taller than 3:4 (a 9:16 screen shot) is cut to 3:4; 4:5 stays 4:5.
    near(photo.printAspect(1080, 1920), 3 / 4);
    near(photo.printAspect(800, 1000), 4 / 5);
    near(photo.printAspect(850, 1000), 4 / 5);
  });

  it("prints a landscape in 4:3 to 3:2", () => {
    near(photo.printAspect(4032, 3024), 4 / 3);
    near(photo.printAspect(6000, 4000), 3 / 2);
    near(photo.printAspect(1920, 1080), 3 / 2);
    near(photo.printAspect(1250, 1000), 4 / 3);
  });

  it("prints near-square as square", () => {
    for (const [w, h] of [
      [1000, 1000],
      [950, 1000],
      [1100, 1000],
    ]) near(photo.printAspect(w, h), 1, `${w}x${h}`);
  });

  it("holds extreme panoramas and slivers to the print shapes", () => {
    near(photo.printAspect(12000, 1000), 3 / 2);
    near(photo.printAspect(1000, 12000), 3 / 4);
    near(photo.printAspect(1, 4000), 3 / 4);
    near(photo.printAspect(4000, 1), 3 / 2);
  });

  it("gives tiny images a shape too, and no shape for no size", () => {
    near(photo.printAspect(1, 1), 1);
    near(photo.printAspect(2, 3), 3 / 4);
    for (const [w, h] of [
      [0, 100],
      [100, 0],
      [Number.NaN, 10],
      [10, Infinity],
      [-4, 3],
    ]) assert.equal(photo.printAspect(w, h), null, `${w}x${h}`);
  });
});

describe("coverCrop", () => {
  const inside = (c: ReturnType<typeof photo.coverCrop>, w: number, h: number) => {
    for (const v of [c.sx, c.sy, c.sw, c.sh]) assert.ok(Number.isFinite(v), `${JSON.stringify(c)}`);
    assert.ok(c.sw >= 1 && c.sh >= 1, `crop ${c.sw}x${c.sh}`);
    assert.ok(c.sx >= 0 && c.sy >= 0 && c.sx + c.sw <= w + 1e-9 && c.sy + c.sh <= h + 1e-9, `crop ${JSON.stringify(c)} outside ${w}x${h}`);
  };

  it("keeps the whole of a photo already in the print's shape", () => {
    assert.deepEqual(photo.coverCrop(3024, 4032, 3 / 4), { sx: 0, sy: 0, sw: 3024, sh: 4032 });
    assert.deepEqual(photo.coverCrop(4032, 3024, 4 / 3), { sx: 0, sy: 0, sw: 4032, sh: 3024 });
  });

  it("leans a portrait's crop to its upper third", () => {
    // A 9:16 portrait into 3:4: 1080 x 1440 of 1920 tall, 480 spare, 160 above.
    const c = photo.coverCrop(1080, 1920, 3 / 4);
    assert.equal(c.sw, 1080);
    assert.equal(c.sh, 1440);
    near(c.sy, 160);
    assert.equal(c.sx, 0);
    // The face in the top fifth of the portrait stays in the print.
    assert.ok(c.sy <= 1920 * 0.1);
    inside(c, 1080, 1920);
  });

  it("cuts a landscape about its middle", () => {
    const c = photo.coverCrop(4000, 1000, 3 / 2);
    assert.equal(c.sh, 1000);
    assert.equal(c.sw, 1500);
    assert.equal(c.sx, 1250);
    assert.equal(c.sy, 0);
    // Wider print than the photo: cut top and bottom evenly.
    const d = photo.coverCrop(4000, 3000, 3 / 2);
    near(d.sy, (3000 - 4000 / 1.5) / 2);
    inside(d, 4000, 3000);
  });

  it("stays inside panoramas, slivers and tiny images", () => {
    for (const [w, h] of [
      [12000, 1000],
      [1000, 12000],
      [1, 4000],
      [4000, 1],
      [1, 1],
      [2, 3],
    ]) {
      for (const a of [3 / 4, 4 / 5, 1, 4 / 3, 3 / 2]) inside(photo.coverCrop(w, h, a), w, h);
    }
  });

  it("does not throw on no size or a bad aspect", () => {
    for (const [w, h, a] of [
      [0, 0, 1],
      [100, 100, 0],
      [100, 100, Number.NaN],
      [Number.NaN, 100, 1],
    ]) {
      const c = photo.coverCrop(w, h, a);
      for (const v of [c.sx, c.sy, c.sw, c.sh]) assert.ok(Number.isFinite(v) && v >= 0);
    }
  });
});

describe("withPrintAspect", () => {
  it("gives an uploaded photo its own shape once it loads", async () => {
    assert.equal((await photo.withPrintAspect({ id: "a", src: "img:3024x4032" })).aspect, 3 / 4);
    assert.equal((await photo.withPrintAspect({ id: "b", src: "img:4032x3024" })).aspect, 4 / 3);
  });

  it("keeps an aspect it was given, and a photo that will not load as it is", async () => {
    assert.equal((await photo.withPrintAspect({ id: "a", src: "img:3024x4032", aspect: 4 / 3 })).aspect, 4 / 3);
    assert.equal((await photo.withPrintAspect({ id: "b", src: "broken" })).aspect, undefined);
    assert.equal((await photo.withPrintAspect({ id: "c", scene: "sea" })).aspect, undefined);
  });
});

/* ------------------------------------------------------------------------ */
/* The story page                                                            */
/* ------------------------------------------------------------------------ */

const PORTRAIT = "img:3024x4032";
const LANDSCAPE = "img:4032x3024";
const TALL = "img:1080x1920";
const PANORAMA = "img:12000x1000";

const SENTENCE = "It was raining on the church steps, and Ana was carrying far too many hymn books. ";
const story = (n: number) => SENTENCE.repeat(Math.ceil(n / SENTENCE.length) + 1).slice(0, n).trim();

function spread(srcs: string[], text = story(220)): BookSpread {
  return {
    id: "m",
    title: "How I met your mother",
    year: 1987,
    tellers: ["David"],
    text,
    photos: srcs.map((src, i): BookPhoto => ({ id: `p${i}`, src })),
  };
}

/** A print's box on the page, grown by its tilt (at most ~0.06 rad). */
function box(p: Print) {
  const t = 0.06;
  const hw = (p.w * Math.cos(t) + p.h * Math.sin(t)) / 2;
  const hh = (p.h * Math.cos(t) + p.w * Math.sin(t)) / 2;
  return { x0: p.cx - hw, x1: p.cx + hw, y0: p.cy - hh, y1: p.cy + hh };
}

async function page(srcs: string[], text?: string) {
  reset();
  await art.drawStoryPage(spread(srcs, text), 2, fonts, 30);
  const words = drawn.texts.filter((t) => / serif$/.test(t.font) && !t.font.startsWith("italic"));
  const lastLine = Math.max(...words.map((t) => t.y), 0);
  return { prints: [...drawn.prints], images: [...drawn.images], lastLine };
}

function assertPlaced(prints: Print[], lastLine: number) {
  const boxes = prints.map(box);
  for (const b of boxes) {
    assert.ok(b.y0 > lastLine + 20, `print top ${b.y0} runs into the words ending at ${lastLine}`);
    assert.ok(b.y1 <= 1300 - 100, `print bottom ${b.y1} runs into the page number`);
    assert.ok(b.x0 >= 60 && b.x1 <= 1000 - 100, `print ${b.x0}..${b.x1} runs off the page or into the gutter`);
  }
  if (boxes.length === 2) {
    const [a, b] = boxes;
    const apart = a.x1 <= b.x0 || b.x1 <= a.x0 || a.y1 <= b.y0 || b.y1 <= a.y0;
    assert.ok(apart, `prints overlap: ${JSON.stringify(boxes)}`);
  }
}

describe("drawStoryPage with uploaded photos", () => {
  it("prints one portrait as a portrait, its face kept", async () => {
    const { prints, images, lastLine } = await page([PORTRAIT]);
    assert.equal(prints.length, 1);
    assert.ok(prints[0].h > prints[0].w * 1.15, `print ${prints[0].w}x${prints[0].h} is not a portrait`);
    assertPlaced(prints, lastLine);
    // The whole 3:4 photo is drawn: nothing of the head is cut.
    const drawnPhoto = images.find((d) => d.image instanceof FakeImage);
    assert.ok(drawnPhoto);
    const [sx, sy, sw, sh] = drawnPhoto.args;
    assert.ok(sy <= 4032 * 0.01, `crop starts ${sy}px down`);
    assert.ok(sx >= 0 && sx + sw <= 3024 + 1 && sh >= 4032 * 0.97, `crop ${drawnPhoto.args.slice(0, 4)}`);
  });

  it("prints one landscape as a landscape", async () => {
    const { prints, lastLine } = await page([LANDSCAPE]);
    assert.equal(prints.length, 1);
    assert.ok(prints[0].w > prints[0].h * 1.15, `print ${prints[0].w}x${prints[0].h} is not a landscape`);
    assertPlaced(prints, lastLine);
  });

  for (const pair of [
    [PORTRAIT, LANDSCAPE],
    [LANDSCAPE, PORTRAIT],
    [PORTRAIT, PORTRAIT],
    [LANDSCAPE, LANDSCAPE],
    [TALL, PANORAMA],
  ]) {
    it(`places a pair ${pair.join(" + ")} apart, clear of the words`, async () => {
      const { prints, lastLine } = await page(pair);
      assert.equal(prints.length, 2);
      assertPlaced(prints, lastLine);
      const portrait = (p: Print) => p.h > p.w;
      pair.forEach((src, i) => assert.equal(portrait(prints[i]), src === PORTRAIT || src === TALL, `print ${i} of ${src}`));
    });
  }

  it("crops a tall portrait toward its top", async () => {
    const { images } = await page([TALL]);
    const d = images.find((x) => x.image instanceof FakeImage);
    assert.ok(d);
    const [, sy, , sh] = d.args;
    const spare = 1920 - sh;
    assert.ok(spare > 0 && Math.abs(sy - spare / 3) < 1, `sy ${sy} for ${spare} spare`);
  });

  it("still keeps every print a positive size, or leaves it out, for any length of words", async () => {
    for (const n of [0, 220, 800, 1400, 3000]) {
      for (const pair of [[PORTRAIT], [LANDSCAPE], [PORTRAIT, LANDSCAPE], [TALL, PANORAMA]]) {
        const { prints, lastLine } = await page(pair, story(n));
        for (const p of prints) assert.ok(p.w >= art.PHOTO_MIN_W && p.h > 0, `${n} chars: a print ${p.w}x${p.h}`);
        if (prints.length) assertPlaced(prints, lastLine);
      }
    }
  });

  it("prints a photo that will not load as the stand-in, 4:3", async () => {
    const { prints } = await page(["broken"]);
    assert.equal(prints.length, 1);
    assert.ok(prints[0].w > prints[0].h);
  });
});
