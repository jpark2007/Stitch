/**
 * The shared context's lifecycle (hubState.ts).
 *
 *   cd web && node --no-warnings --test "components/audio/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Hub = typeof import("./hubState");
const path = "./hubState.ts";
const h: Hub = await import(path);

type Ev = Parameters<Hub["hubStep"]>[1];

/** Run events from a state; collect every effect in order. */
function run(events: Ev[], from = h.INITIAL_HUB) {
  let state = from;
  const effects: string[] = [];
  for (const ev of events) {
    const r = h.hubStep(state, ev);
    state = r.state;
    effects.push(...r.effects);
  }
  return { state, effects };
}

describe("hub lifecycle", () => {
  it("makes nothing and listens for nothing until an engine attaches", () => {
    const r = run([{ type: "gesture" }, { type: "awake", on: true }]);
    assert.equal(r.state.status, "none");
    assert.deepEqual(r.effects, ["unlockBook"]);
  });

  it("listens while engines are attached, and only then", () => {
    const r = run([{ type: "attach" }, { type: "attach" }, { type: "detach" }]);
    assert.deepEqual(r.effects, ["listen"]);
    assert.deepEqual(run([{ type: "detach" }], r.state).effects, ["unlisten"]);
    // A stray detach never goes negative or unlistens twice.
    const z = run([{ type: "detach" }, { type: "detach" }], r.state);
    assert.equal(z.state.engines, 0);
    assert.deepEqual(z.effects, ["unlisten"]);
  });

  it("creates the context only inside a gesture, then sleeps it until wanted", () => {
    const a = run([{ type: "attach" }]);
    assert.equal(a.state.status, "none");
    const g = run([{ type: "gesture" }], a.state);
    assert.deepEqual(g.effects, ["unlockBook", "create", "suspend"]);
    assert.equal(g.state.status, "suspended");
    const w = run([{ type: "awake", on: true }], g.state);
    assert.deepEqual(w.effects, ["resume"]);
    assert.equal(w.state.status, "running");
  });

  it("a gesture while muted makes nothing; the unmute click does", () => {
    const m = run([{ type: "attach" }, { type: "mute", on: true }, { type: "gesture" }]);
    assert.equal(m.state.status, "none");
    assert.deepEqual(m.effects, ["listen"]);
    const u = run([{ type: "mute", on: false }], m.state);
    assert.deepEqual(u.effects, ["create", "suspend"]);
  });

  it("suspends when the last engine sleeps, resumes when one wakes", () => {
    const on = run([{ type: "attach" }, { type: "gesture" }, { type: "awake", on: true }]).state;
    const off = run([{ type: "awake", on: false }], on);
    assert.deepEqual(off.effects, ["suspend"]);
    assert.equal(off.state.status, "suspended");
    assert.deepEqual(run([{ type: "awake", on: false }], off.state).effects, []);
    assert.deepEqual(run([{ type: "awake", on: true }], off.state).effects, ["resume"]);
  });

  it("a gesture resumes a context an awake engine holds (iOS needs one to resume)", () => {
    const on = run([{ type: "attach" }, { type: "gesture" }, { type: "awake", on: true }]).state;
    const g = run([{ type: "gesture" }], on);
    assert.deepEqual(g.effects, ["unlockBook", "resume"]);
    // ...and the unmute click counts as one.
    assert.deepEqual(run([{ type: "mute", on: true }, { type: "mute", on: false }], on).effects, ["resume"]);
    // No engine awake: a gesture leaves a sleeping context asleep.
    const asleep = run([{ type: "awake", on: false }], on).state;
    assert.deepEqual(run([{ type: "gesture" }], asleep).effects, ["unlockBook"]);
  });

  it("muting is the engines' business: the hub only stops making contexts", () => {
    const on = run([{ type: "attach" }, { type: "gesture" }, { type: "awake", on: true }]).state;
    const m = run([{ type: "mute", on: true }], on);
    assert.deepEqual(m.effects, []);
    assert.equal(m.state.status, "running");
  });

  it("the context is never made twice", () => {
    const r = run([{ type: "attach" }, { type: "gesture" }, { type: "gesture" }, { type: "mute", on: false }, { type: "gesture" }]);
    assert.equal(r.effects.filter((e) => e === "create").length, 1);
  });
});

describe("hub dispatcher", () => {
  /**
   * hub.ts in miniature: a fake context that records its calls and whose
   * state settles in call order, and engines whose attach callback runs
   * inside "create" (engine.ts wakes there when it already has an era).
   */
  function fakeHub(wakeOnCreate: boolean) {
    const calls: string[] = [];
    let settled = "none";
    const awake = new Set<object>();
    const clients: (() => void)[] = [];
    const d = h.createHubDispatcher((fx) => {
      calls.push(fx);
      if (fx === "create") {
        settled = "running";
        for (const cb of clients) cb();
      } else if (fx === "resume") settled = "running";
      else if (fx === "suspend") settled = "suspended";
    });
    const owner = {};
    const wake = () => {
      const had = awake.size > 0;
      awake.add(owner);
      if (!had) d.dispatch({ type: "awake", on: true });
    };
    clients.push(() => {
      if (wakeOnCreate) wake();
    });
    d.dispatch({ type: "attach" });
    return { d, calls, wake, context: () => settled };
  }

  it("an engine that wakes inside create leaves the context running", () => {
    const f = fakeHub(true);
    f.d.dispatch({ type: "gesture" });
    // The nested wake waits for the gesture's own effects: its resume is last.
    assert.deepEqual(f.calls, ["listen", "unlockBook", "create", "suspend", "resume"]);
    assert.equal(f.context(), "running");
    assert.equal(f.d.state.status, "running");
    assert.equal(f.d.state.awake, true);
  });

  it("an engine with nothing to play yet leaves it asleep until it wakes", () => {
    const f = fakeHub(false);
    f.d.dispatch({ type: "gesture" });
    assert.equal(f.context(), "suspended");
    f.wake();
    assert.equal(f.context(), "running");
    assert.deepEqual(f.calls.slice(-2), ["suspend", "resume"]);
  });

  it("events raised by an effect run in order, each once", () => {
    const seen: string[] = [];
    const d: ReturnType<typeof h.createHubDispatcher> = h.createHubDispatcher((fx) => {
      seen.push(fx);
      // listen() reads the current mute and dispatches it from inside the effect.
      if (fx === "listen") {
        d.dispatch({ type: "mute", on: true });
        d.dispatch({ type: "gesture" });
      }
    });
    d.dispatch({ type: "attach" });
    assert.deepEqual(seen, ["listen"]);
    assert.equal(d.state.muted, true);
    assert.equal(d.state.status, "none");
  });

  it("an effect that throws does not leave the hub deaf", () => {
    let boom = true;
    const seen: string[] = [];
    const d = h.createHubDispatcher((fx) => {
      seen.push(fx);
      if (boom && fx === "listen") throw new Error("boom");
    });
    assert.throws(() => d.dispatch({ type: "attach" }));
    boom = false;
    d.dispatch({ type: "gesture" });
    assert.deepEqual(seen, ["listen", "unlockBook", "create", "suspend"]);
  });
});

describe("music hub (fake context, state settling asynchronously)", () => {
  /** A context whose state changes only when its promise settles, like WebKit's. */
  class FakeContext {
    state = "running";
    calls: string[];
    settleMs: number;
    constructor(calls: string[], settleMs: number) {
      this.calls = calls;
      this.settleMs = settleMs;
      calls.push("new");
    }
    settle(to: string) {
      this.calls.push(to === "running" ? "resume" : "suspend");
      return new Promise<void>((r) =>
        setTimeout(() => {
          this.state = to;
          r();
        }, this.settleMs),
      );
    }
    resume() {
      return this.settle("running");
    }
    suspend() {
      return this.settle("suspended");
    }
  }

  function setup(opts: { muted?: boolean; settleMs?: number } = {}) {
    const calls: string[] = [];
    let muted = opts.muted ?? false;
    const muteSubs = new Set<(m: boolean) => void>();
    let gesture: (() => void) | null = null;
    const hub = h.createMusicHub<FakeContext>({
      isMuted: () => muted,
      subscribeMuted(cb) {
        muteSubs.add(cb);
        return () => muteSubs.delete(cb);
      },
      unlockAudio: () => calls.push("unlockBook"),
      makeContext: () => new FakeContext(calls, opts.settleMs ?? 20),
      listen(on, g) {
        gesture = on ? g : null;
      },
    });
    return {
      hub,
      calls,
      tap: () => gesture?.(),
      listening: () => gesture !== null,
      setMuted(m: boolean) {
        muted = m;
        for (const cb of [...muteSubs]) cb(m);
      },
      settled: () => new Promise((r) => setTimeout(r, 4 * (opts.settleMs ?? 20))),
    };
  }

  it("an engine that already has an era, waking inside create: one tap leaves it running", async () => {
    const t = setup();
    const owner = {};
    // engine.ts's attach callback: build the graph, and wake (era known, book open).
    t.hub.attach(() => t.hub.wake(owner));
    t.tap();
    await t.settled();
    assert.deepEqual(t.calls, ["unlockBook", "new", "suspend", "resume"]);
    assert.equal(t.hub.context()?.state, "running");
    assert.equal(t.hub.state().status, "running");
    assert.equal(t.hub.awake(), true);
  });

  it("the same with a context that settles at once (microtasks)", async () => {
    const t = setup({ settleMs: 0 });
    const owner = {};
    t.hub.attach(() => t.hub.wake(owner));
    t.tap();
    await t.settled();
    assert.equal(t.hub.context()?.state, "running");
  });

  it("nothing to play at the tap: made, then asleep until an engine wakes", async () => {
    const t = setup();
    const owner = {};
    t.hub.attach(() => {});
    t.tap();
    await t.settled();
    assert.equal(t.hub.context()?.state, "suspended");
    t.hub.wake(owner);
    await t.settled();
    assert.equal(t.hub.context()?.state, "running");
    t.hub.sleep(owner);
    await t.settled();
    assert.equal(t.hub.context()?.state, "suspended");
  });

  it("muted: a tap makes nothing; the unmute click makes it", async () => {
    const t = setup({ muted: true });
    const owner = {};
    t.hub.attach(() => t.hub.wake(owner));
    t.tap();
    assert.equal(t.hub.context(), null);
    t.setMuted(false);
    await t.settled();
    assert.equal(t.hub.context()?.state, "running");
  });

  it("listens only while an engine is attached", () => {
    const t = setup();
    const detach = t.hub.attach(() => {});
    assert.equal(t.listening(), true);
    detach();
    detach();
    assert.equal(t.listening(), false);
    assert.equal(t.hub.state().engines, 0);
  });
});

