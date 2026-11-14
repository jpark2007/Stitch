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
