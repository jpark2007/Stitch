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

// The controller eases by wall time: a clock the tests step, 60 frames a second.
let clock = 1000;
performance.now = () => clock;

type SceneViewModule = typeof import("./sceneView");
type ControllerModule = typeof import("./controller");
type LeafShapeModule = typeof import("./leafShape");
type Three = typeof import("three");
const paths = ["./sceneView.ts", "./controller.ts", "./leafShape.ts", "three"];
const sv: SceneViewModule = await import(paths[0]);
const C: ControllerModule = await import(paths[1]);
const { PAGE_W }: LeafShapeModule = await import(paths[2]);
const T: Three = await import(paths[3]);
const { SceneView, readSceneViewPref, writeSceneViewPref, escapeAction, stepViewShot, chooseViewShot, viewSwitch } = sv;

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

describe("viewSwitch", () => {
  it("names the view it goes to: the whole book from the cutscene, the cutscene from the whole book", () => {
    assert.deepEqual(viewSwitch("scene"), {
      to: "book",
      label: "Whole book",
      action: "See the whole book",
      title: "See the whole book (Esc)",
    });
    assert.deepEqual(viewSwitch("book"), {
      to: "scene",
      label: "Cutscene",
      action: "Back to the cutscene",
      title: "Back to the cutscene (Esc)",
    });
  });

  it("its spoken action contains the visible label, so voice control finds it", () => {
    for (const mode of ["scene", "book"] as const) {
      const sw = viewSwitch(mode);
      assert.ok(sw.action.toLowerCase().includes(sw.label.toLowerCase()), sw.action);
    }
  });

  it("choosing what it offers switches the view, and then it offers the way back", () => {
    const view = new SceneView("scene");
    view.offer(true);
    assert.equal(view.choose(viewSwitch(view.mode).to), true);
    assert.equal(view.mode, "book");
    assert.equal(viewSwitch(view.mode).label, "Cutscene");
    assert.equal(view.choose(viewSwitch(view.mode).to), true);
    assert.equal(view.mode, "scene");
    assert.equal(viewSwitch(view.mode).label, "Whole book");
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

/* ------------------------------------------------------------------------ */
/* Through the book's own controller                                         */
/* ------------------------------------------------------------------------ */

type Vec = InstanceType<Three["Vector3"]>;

/**
 * The real BookController on the spread with a playing moment, posed as
 * Book3D's frame loop does: frame(), then the stage writes its track (as
 * ShotFeed does, every frame, whatever the camera does), then poseCamera().
 */
function book(opts: { width?: number; height?: number; mode?: "scene" | "book"; moment?: boolean } = {}) {
  const { width = 1440, height = 900, mode = "scene", moment = true } = opts;
  const saved: string[] = [];
  const view = new SceneView(mode, (m) => saved.push(m));
  const ctl = new C.BookController(3, { opened: true, spread: 1, turn: null, focus: 0 }, () => {}, () => {}, () => {}, view);
  const camera = new T.PerspectiveCamera(30, width / height, 0.4, 40);
  const scene = new T.Scene();
  scene.fog = new T.Fog(0, 4, 11);
  const parts = {
    book: null,
    coverPivot: null,
    leftBlock: null,
    spine: null,
    shadow: null,
    stage: null,
    closedHit: null,
    blocks: { left: { update() {} }, right: { update() {} } },
    spineGeometry: null,
  } as unknown as Parameters<InstanceType<ControllerModule["BookController"]>["frame"]>[0]["parts"];
  let t = 0;
  /** The timeline's camera: a slow dolly across the set (world space). */
  const track = (time: number) => [0.5 + 0.12 * Math.sin(time), 0.62, 0.42, 0.5 + 0.05 * Math.sin(time), 0.1, 0, 28] as const;
  const shot = ctl.shots[0];
  /** One frame; `tick` false when a second book is drawn in step with this one (one shared clock). */
  const frame = (tick = true) => {
    if (tick) clock += 1000 / 60;
    t += 1 / 60;
    ctl.frame({ camera, scene, width, height, parts });
    if (moment) shot.frame(...track(t));
    ctl.poseCamera(camera, scene);
  };
  const run = (seconds: number, each?: () => void) => {
    for (let i = 0; i < Math.round(seconds * 60); i++) {
      frame();
      each?.();
    }
  };
  /** Frames until `done` holds (seconds), or Infinity. */
  const until = (done: () => boolean, max = 4) => {
    for (let i = 1; i <= max * 60; i++) {
      frame();
      if (done()) return i / 60;
    }
    return Infinity;
  };
  const start = () => {
    run(0.2);
    if (moment) shot.offer(true);
  };
  const tap = (x: number) => {
    const pt = { x: x * PAGE_W, z: 0 };
    assert.equal(ctl.pageDown(pt, 600, 400), true);
    ctl.dragEnd(600, 400);
  };
  return { ctl, view, saved, camera, scene, shot, frame, run, until, start, tap, now: () => t };
}

/** The reading view of the whole open spread at this aspect (frameCamera, focus 0). */
function readingView(width: number, height: number, focus = 0) {
  const pos = new T.Vector3();
  const target = new T.Vector3();
  C.frameCamera({ open: 1, lift: 0, focus, single: 0, px: 0, py: 0 }, width / height, 30, pos, target);
  return pos;
}

const near = (a: Vec, b: Vec, eps = 1e-6) => a.distanceTo(b) < eps;

describe("scene and book view through the book controller", () => {
  it("leans into a playing moment's shot and offers the switch", () => {
    const b = book();
    b.start();
    const t = b.until(() => b.ctl.shotWeight === 1);
    assert.ok(t > 1.6 && t < 2, `leaned in in ${t}`);
    assert.equal(b.view.available, true);
    assert.equal(b.ctl.inShot, true);
  });

  it("eases out to the whole spread in book view and stays there while the track moves", () => {
    const b = book();
    b.start();
    b.until(() => b.ctl.shotWeight === 1);
    assert.equal(b.view.choose("book"), true);
    const t = b.until(() => b.ctl.shotWeight === 0);
    assert.ok(t >= 0.6 && t <= 0.9, `out in ${t}`);
    assert.ok(near(b.camera.position, readingView(1440, 900)), "the reading view of the whole spread");
    const at = b.camera.position.clone();
    b.run(1.5, () => assert.ok(near(b.camera.position, at), "the book's camera holds while the timeline's moves"));
    assert.equal(b.view.available, true);
    assert.equal(b.ctl.inShot, false);
  });

  it("goes back into the scene at the timeline's current camera, not where it left it", () => {
    const b = book();
    const ref = book();
    b.start();
    ref.start();
    const both = (n: number) => {
      for (let i = 0; i < n; i++) {
        b.frame();
        ref.frame(false);
      }
    };
    both(150);
    b.view.choose("book");
    both(120);
    b.view.choose("scene");
    let t = 0;
    while (b.ctl.shotWeight < 1 && t < 120) {
      both(1);
      t++;
    }
    assert.ok(t / 60 >= 0.6 && t / 60 <= 0.9, `back in ${t / 60}`);
    assert.ok(near(b.camera.position, ref.camera.position, 1e-9), "the same camera as a reader who never left the scene");
    const was = b.camera.position.clone();
    both(30);
    assert.ok(!near(b.camera.position, was, 1e-4), "and it follows the track on");
    assert.ok(near(b.camera.position, ref.camera.position, 1e-9));
  });

  it("does not move when the telling ends in book view; eases out at the usual pace in scene view", () => {
    const b = book({ mode: "book" });
    b.start();
    b.run(2, () => assert.equal(b.ctl.shotWeight, 0, "a stored book view never leans in"));
    assert.equal(b.view.available, true);
    const at = b.camera.position.clone();
    b.shot.offer(false);
    b.run(2, () => assert.ok(near(b.camera.position, at)));
    assert.equal(b.view.available, false);
    assert.equal(b.view.mode, "book");

    const s = book();
    s.start();
    s.until(() => s.ctl.shotWeight === 1);
    s.shot.offer(false);
    const t = s.until(() => s.ctl.shotWeight === 0);
    assert.ok(Math.abs(t - sv.SHOT_OUT) < 0.05, `out in ${t}`);
    assert.equal(s.view.available, false);
  });

  it("cuts under reduced motion, and holds the scene's close-up still", () => {
    const b = book();
    b.ctl.setCalm(true);
    b.start();
    b.frame();
    assert.equal(b.ctl.shotWeight, 1);
    const at = b.camera.position.clone();
    b.run(1.5, () => assert.ok(near(b.camera.position, at), "still while the track moves"));
    b.view.choose("book");
    b.frame();
    assert.equal(b.ctl.shotWeight, 0);
    b.view.choose("scene");
    b.frame();
    assert.equal(b.ctl.shotWeight, 1);
    assert.ok(near(b.camera.position, at));
    b.shot.offer(false);
    b.frame();
    assert.equal(b.ctl.shotWeight, 0);
  });

  it("taps: the pop-up goes back to the scene, the story page looks closer, the shot steps out to the book", () => {
    const b = book({ mode: "book" });
    b.start();
    b.run(0.5);
    b.tap(-0.25);
    assert.equal(b.view.mode, "book");
    assert.equal(b.ctl.focus, -1);
    b.tap(0.25);
    assert.equal(b.view.mode, "scene");
    assert.deepEqual(b.saved, ["scene"]);
    b.until(() => b.ctl.shotWeight === 1);
    assert.equal(b.ctl.focus, 0, "the scene leans in from the whole spread");
    b.tap(0.25);
    assert.equal(b.view.mode, "book");
    assert.deepEqual(b.saved, ["scene", "book"], "the reader's choice is kept");
  });

  it("a phone widens from the stage page to the whole spread, never pausing the scene", () => {
    const b = book({ width: 390, height: 844 });
    b.run(0.2);
    b.ctl.setFocus(1);
    b.until(() => b.ctl.stageView.current === 1);
    b.shot.offer(true);
    b.until(() => b.ctl.shotWeight === 1);
    b.view.choose("book");
    b.run(2, () => assert.equal(b.ctl.stageView.current, 1));
    assert.equal(b.ctl.shotWeight, 0);
    assert.equal((b.ctl as unknown as { single: number }).single, 0);
    assert.ok(near(b.camera.position, readingView(390, 844, 1)), "the whole spread, centred");
    // Paging on in book view turns the leaf; it does not pan to a page.
    b.ctl.step(-1);
    assert.equal(b.ctl.focus, 1);
    b.run(0.1);
    // A tap on the story page changes nothing; on the pop-up, back to the scene.
    b.tap(-0.25);
    assert.equal(b.view.mode, "book");
    assert.equal(b.ctl.focus, 1);
    b.tap(0.25);
    assert.equal(b.view.mode, "scene");
    b.run(1, () => assert.equal(b.ctl.stageView.current, 1));
    assert.equal(b.ctl.shotWeight, 1);
  });

  it("a phone keeps the whole book after the telling ends, until the reader pans", () => {
    const b = book({ width: 390, height: 844, mode: "book" });
    b.run(0.2);
    b.ctl.setFocus(1);
    b.until(() => b.ctl.stageView.current === 1);
    b.shot.offer(true);
    b.run(2);
    const at = b.camera.position.clone();
    b.shot.offer(false);
    b.run(1.5, () => assert.ok(near(b.camera.position, at)));
    b.tap(-0.25);
    b.run(2.5);
    assert.equal(b.ctl.focus, -1);
    assert.equal((b.ctl as unknown as { single: number }).single, 1);
  });

  it("a spread without a moment is as before, whatever the stored view", () => {
    const b = book({ mode: "book", moment: false });
    const ref = book({ mode: "scene", moment: false });
    b.start();
    ref.start();
    for (let i = 0; i < 120; i++) {
      b.frame();
      ref.frame(false);
      assert.ok(near(b.camera.position, ref.camera.position, 1e-12));
    }
    assert.equal(b.view.available, false);
    b.tap(0.25);
    ref.tap(0.25);
    assert.equal(b.ctl.focus, 1);
    assert.equal(ref.ctl.focus, 1);
    assert.equal(b.view.mode, "book");
    assert.deepEqual(b.saved, []);
  });

  it("a page turn takes the switch away until the next moment plays", () => {
    const b = book({ mode: "book" });
    b.start();
    b.run(0.5);
    assert.equal(b.view.available, true);
    b.ctl.next();
    b.frame();
    assert.equal(b.view.available, false);
  });

  it("pointer parallax still drifts the camera in book view", () => {
    const b = book({ mode: "book" });
    b.ctl.setParallax(true);
    b.start();
    b.run(1);
    const at = b.camera.position.clone();
    b.ctl.pointer(1, 0);
    b.run(1);
    assert.ok(!near(b.camera.position, at, 1e-3));
    assert.equal(b.ctl.shotWeight, 0);
  });
});
