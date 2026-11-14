/**
 * Scene view or book view (sceneView.ts): the reader's choice, kept per
 * browser, and the camera's ease between the stage's shot and the whole
 * open spread while the scene keeps playing.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The feature: opening a memory leans the camera into the pop-up like a cut
 * scene, and the reader had no way to see the whole book while it played.
 */

import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import { describe, it } from "node:test";

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

type SceneViewModule = typeof import("./sceneView");
const paths = ["./sceneView.ts"];
const sv: SceneViewModule = await import(paths[0]);
const { SceneView, readSceneViewPref, writeSceneViewPref, escapeAction, stepViewShot, chooseViewShot } = sv;

const DT = 1 / 60;

function memoryStore(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v;
    },
  };
}

describe("the reader's stored view", () => {
  it("defaults to the scene, reads a stored book view, ignores anything else", () => {
    assert.equal(readSceneViewPref(memoryStore()), "scene");
    assert.equal(readSceneViewPref(memoryStore({ [sv.SCENE_VIEW_KEY]: "book" })), "book");
    assert.equal(readSceneViewPref(memoryStore({ [sv.SCENE_VIEW_KEY]: "BOOK!" })), "scene");
    assert.equal(readSceneViewPref(memoryStore({ [sv.SCENE_VIEW_KEY]: "scene" })), "scene");
  });

  it("survives storage that throws, or none at all (the server)", () => {
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    assert.equal(readSceneViewPref(broken), "scene");
    assert.doesNotThrow(() => writeSceneViewPref("book", broken));
    assert.equal(readSceneViewPref(null), "scene");
    assert.doesNotThrow(() => writeSceneViewPref("book", null));
    // No window in node: the defaults find no storage.
    assert.equal(readSceneViewPref(), "scene");
    assert.doesNotThrow(() => writeSceneViewPref("book"));
  });

  it("writes the choice under its key", () => {
    const store = memoryStore();
    writeSceneViewPref("book", store);
    assert.equal(store.data[sv.SCENE_VIEW_KEY], "book");
    assert.equal(readSceneViewPref(store), "book");
  });
});

describe("SceneView", () => {
  function make(mode?: "scene" | "book") {
    const saved: string[] = [];
    const view = new SceneView(mode, (m) => saved.push(m));
    let emits = 0;
    view.subscribe(() => emits++);
    return { view, saved, emits: () => emits };
  }

  it("cannot be switched while no moment plays", () => {
    const { view, saved, emits } = make();
    assert.equal(view.choose("book"), false);
    assert.equal(view.toggle(), false);
    assert.equal(view.mode, "scene");
    assert.equal(emits(), 0);
    assert.deepEqual(saved, []);
  });

  it("emits once when a moment starts, and keeps one snapshot while it plays", () => {
    const { view, emits } = make();
    const before = view.get();
    view.offer(true);
    assert.equal(emits(), 1);
    const playing = view.get();
    assert.notEqual(playing, before);
    assert.equal(playing.available, true);
    for (let i = 0; i < 100; i++) view.offer(true);
    assert.equal(emits(), 1);
    assert.equal(view.get(), playing);
  });

  it("toggles, keeps the choice and says so once", () => {
    const { view, saved, emits } = make();
    view.offer(true);
    assert.equal(view.toggle(), true);
    assert.equal(view.mode, "book");
    assert.deepEqual(saved, ["book"]);
    assert.equal(emits(), 2);
    assert.equal(view.choose("book"), false, "choosing the view already shown is no change");
    assert.equal(emits(), 2);
    assert.equal(view.toggle(), true);
    assert.equal(view.mode, "scene");
    assert.deepEqual(saved, ["book", "scene"]);
  });

  it("holds the choice across the end of one moment and the start of the next", () => {
    const { view } = make();
    view.offer(true);
    view.choose("book");
    view.offer(false);
    assert.equal(view.mode, "book");
    assert.equal(view.available, false);
    view.offer(true);
    assert.deepEqual(view.get(), { mode: "book", available: true });
  });

  it("starts in the stored mode", () => {
    assert.equal(new SceneView("book").mode, "book");
    assert.equal(new SceneView().mode, "scene");
  });

  it("unsubscribes", () => {
    const view = new SceneView();
    let n = 0;
    const off = view.subscribe(() => n++);
    off();
    view.offer(true);
    assert.equal(n, 0);
  });
});

describe("escapeAction", () => {
  it("toggles the view while a moment plays, else steps back to the spread", () => {
    assert.equal(escapeAction(true), "toggle");
    assert.equal(escapeAction(false), "focus");
  });
});

describe("stepViewShot", () => {
  /** Seconds of 60 fps frames until the weight reaches `to`. */
  function run(s: { w: number; quickTo: number }, to: number, args: { want?: boolean; reading?: boolean; turning?: boolean; mode?: "scene" | "book"; calm?: boolean }, max = 5) {
    const { want = true, reading = true, turning = false, mode = "scene", calm = false } = args;
    let t = 0;
    while (s.w !== to && t < max) {
      stepViewShot(s, want, reading, turning, mode, calm, DT);
      t += DT;
    }
    return t;
  }

  it("leans in over SHOT_IN when a telling starts", () => {
    const s = { w: 0, quickTo: -1 };
    const t = run(s, 1, {});
    assert.ok(Math.abs(t - sv.SHOT_IN) < 0.05, `took ${t}`);
  });

  it("switches to the whole book and back within 0.6..0.9 s", () => {
    const s = { w: 1, quickTo: -1 };
    chooseViewShot(s, "book");
    const out = run(s, 0, { mode: "book" });
    assert.ok(out >= 0.6 && out <= 0.9, `out took ${out}`);
    assert.equal(s.quickTo, -1);
    chooseViewShot(s, "scene");
    const back = run(s, 1, { mode: "scene" });
    assert.ok(back >= 0.6 && back <= 0.9, `back took ${back}`);
    assert.equal(s.quickTo, -1);
  });

  it("gives way to a turning page at SHOT_TURN, whatever the view", () => {
    const s = { w: 1, quickTo: -1 };
    chooseViewShot(s, "scene");
    assert.equal(stepViewShot(s, true, true, true, "scene", false, DT), 0);
    const t = DT + run(s, 0, { turning: true });
    assert.ok(Math.abs(t - sv.SHOT_TURN) < 0.05, `took ${t}`);
  });

  it("eases out at the usual pace when the telling ends in scene view, even mid-switch", () => {
    const s = { w: 0.5, quickTo: -1 };
    chooseViewShot(s, "scene");
    stepViewShot(s, false, true, false, "scene", false, DT);
    assert.equal(s.quickTo, -1);
    const full = { w: 1, quickTo: -1 };
    const t = run(full, 0, { want: false });
    assert.ok(Math.abs(t - sv.SHOT_OUT) < 0.05, `took ${t}`);
  });

  it("never moves when the telling ends in book view", () => {
    const s = { w: 0, quickTo: -1 };
    for (let i = 0; i < 120; i++) {
      stepViewShot(s, i < 60, true, false, "book", false, DT);
      assert.equal(s.w, 0);
    }
  });

  it("cuts under reduced motion, and a changed goal mid-ease clears the quick pace", () => {
    const s = { w: 1, quickTo: -1 };
    chooseViewShot(s, "book");
    stepViewShot(s, true, true, false, "book", true, DT);
    assert.equal(s.w, 0);
    stepViewShot(s, true, true, false, "scene", true, DT);
    assert.equal(s.w, 1);
    stepViewShot(s, false, true, false, "scene", true, DT);
    assert.equal(s.w, 0);
    const m = { w: 1, quickTo: -1 };
    chooseViewShot(m, "book");
    stepViewShot(m, true, true, false, "book", false, DT);
    assert.equal(m.quickTo, 0);
    stepViewShot(m, true, true, false, "scene", false, DT);
    assert.equal(m.quickTo, -1, "the reader went back before it landed: the usual pace in");
  });

  it("lands at once on the first frame (dt 0)", () => {
    const s = { w: 0, quickTo: -1 };
    stepViewShot(s, true, true, false, "scene", false, 0);
    assert.equal(s.w, 1);
  });
});
