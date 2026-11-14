/**
 * BookSpread.dateLine: words printed where the year goes ("Every Christmas
 * Eve"), shown by the contents and the navigation dots in place of the year.
 * Unset, everything shows the year as before.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The page is drawn on a stand-in 2D canvas that records its text (see
 * pageArt.test.ts). Contents.tsx is compiled for node with TypeScript's
 * transpiler by a load hook, so contentsOf is tested as the app runs it.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as nodeModule from "node:module";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import type { BookSpread } from "./types";

type Next = (specifier: string, context: unknown) => unknown;
type LoadNext = (url: string, context: unknown) => unknown;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: {
    resolve(specifier: string, context: unknown, next: Next): unknown;
    load(url: string, context: unknown, next: LoadNext): unknown;
  }): unknown;
};
type TS = typeof import("typescript");
const ts: TS = (await import(["typescript"][0])).default;
const webRoot = new URL("../../", import.meta.url);
registerHooks({
  resolve(specifier, context, next) {
    const spec = specifier.startsWith("@/") ? new URL(specifier.slice(2), webRoot).href : specifier;
    try {
      return next(spec, context);
    } catch (e) {
      if (/^(\.|file:)/.test(spec) && !/\.[cm]?[jt]sx?$/.test(spec)) {
        try {
          return next(`${spec}.ts`, context);
        } catch {
          return next(`${spec}.tsx`, context);
        }
      }
      throw e;
    }
  },
  load(url, context, next) {
    if (!url.startsWith("file:") || !url.endsWith(".tsx")) return next(url, context);
    const source = readFileSync(fileURLToPath(url), "utf8");
    const out = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    });
    return { format: "module", source: out.outputText, shortCircuit: true };
  },
});

/* A stand-in canvas that records what is printed. */
const printed: { text: string; font: string }[] = [];
const noop = () => undefined;
function fakeContext() {
  const state: Record<string | symbol, unknown> = {
    font: "10px sans-serif",
    measureText(text: string) {
      const size = Number(/([\d.]+)px/.exec(String(state.font))?.[1] ?? 10);
      return { width: text.length * size * 0.5, actualBoundingBoxRight: size * 0.5 };
    },
    fillText(text: string) {
      printed.push({ text, font: String(state.font) });
    },
    createLinearGradient: () => ({ addColorStop: noop }),
    createRadialGradient: () => ({ addColorStop: noop }),
    createPattern: () => null,
    createImageData: (w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h }),
  };
  return new Proxy(state, { get: (t, k) => (k in t ? t[k] : noop), set: (t, k, v) => ((t[k] = v), true) });
}
class FakeCanvas {
  width = 300;
  height = 150;
  getContext() {
    return fakeContext();
  }
}
Object.assign(globalThis, {
  document: { createElement: () => new FakeCanvas(), documentElement: {}, fonts: { load: async () => [] } },
  Path2D: class {
    constructor() {
      return new Proxy(this, { get: () => noop });
    }
  },
  HTMLImageElement: class {},
  Image: class {},
});

type Types = typeof import("./types");
type PageArt = typeof import("./pageArt");
type Contents = typeof import("./Contents");
const paths = ["./types.ts", "./pageArt.ts", "./Contents.tsx"];
const types: Types = await import(paths[0]);
const art: PageArt = await import(paths[1]);
const contents: Contents = await import(paths[2]);

const fonts = { display: "serif", hand: "cursive", sans: "sans-serif" };

function spread(extra: Partial<BookSpread> = {}): BookSpread {
  return {
    id: "m-eve",
    title: "Tamales at Abuela's",
    year: 1994,
    tellers: ["Rosa"],
    text: "Everyone came, and everyone stayed too long.",
    photos: [],
    ...extra,
  };
}

/** What the story page prints in the year's hand. */
async function yearLine(s: BookSpread) {
  printed.length = 0;
  await art.drawStoryPage(s, 2, fonts, 30);
  return printed.filter((p) => /^600 \d+px cursive$/.test(p.font)).map((p) => p.text);
}

describe("spreadDate", () => {
  it("is the dateLine when set", () => {
    assert.equal(types.spreadDate(spread({ dateLine: "Every Christmas Eve" })), "Every Christmas Eve");
    assert.equal(types.spreadDate(spread({ dateLine: "  Summers at the lake " })), "Summers at the lake");
  });
  it("is the year otherwise", () => {
    assert.equal(types.spreadDate(spread()), "1994");
    assert.equal(types.spreadDate(spread({ dateLine: "" })), "1994");
    assert.equal(types.spreadDate(spread({ dateLine: "   " })), "1994");
  });
  it("is empty for an undated moment with no dateLine", () => {
    assert.equal(types.spreadDate(spread({ year: 0 })), "");
    assert.equal(types.spreadDate(spread({ year: Number.NaN })), "");
    assert.equal(types.spreadDate(spread({ year: 0, dateLine: "Every Christmas Eve" })), "Every Christmas Eve");
  });
});

describe("the story page", () => {
  it("prints the dateLine on the year's line when set", async () => {
    assert.deepEqual(await yearLine(spread({ dateLine: "Every Christmas Eve" })), ["Every Christmas Eve"]);
  });
  it("prints the year when unset", async () => {
    assert.deepEqual(await yearLine(spread()), ["1994"]);
    assert.deepEqual(await yearLine(spread({ dateLine: " " })), ["1994"]);
  });
  it("sets a long dateLine a size down, never past the measure", async () => {
    const lines = await yearLine(spread({ dateLine: "Every summer at the lake house, the whole family" }));
    assert.equal(lines.length, 1);
    const font = printed.find((p) => p.text === lines[0])!.font;
    assert.ok(Number(/(\d+)px/.exec(font)![1]) < 76);
  });
});

describe("the stage page", () => {
  const label = (s: BookSpread) => {
    printed.length = 0;
    art.drawStagePage(s, 3, fonts, 11);
    return printed.map((p) => p.text).find((t) => t.includes(" · "));
  };
  it("labels the slot with the dateLine when set, the year otherwise", () => {
    assert.equal(label(spread({ dateLine: "Every Christmas Eve" })), "TAMALES AT ABUELA'S · Every Christmas Eve");
    assert.equal(label(spread()), "TAMALES AT ABUELA'S · 1994");
  });
});

describe("contentsOf", () => {
  it("dates an entry by its dateLine when set, by its year otherwise", () => {
    const entries = contents.contentsOf([
      spread({ id: "a", dateLine: "Every Christmas Eve" }),
      spread({ id: "b" }),
      spread({ id: "c", year: 0 }),
      spread({ id: "end", kind: "end", title: "The end" }),
    ]);
    assert.deepEqual(
      entries.map((e) => [e.id, e.year]),
      [
        ["a", "Every Christmas Eve"],
        ["b", "1994"],
        ["c", ""],
      ],
    );
  });
});
