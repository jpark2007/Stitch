/**
 * Crossfade curves and the transport state machine (fades.ts).
 *
 *   cd web && node --no-warnings --test "components/audio/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Fades = typeof import("./fades");
const path = "./fades.ts";
const f: Fades = await import(path);

const close = (a: number, b: number, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} ≈ ${b}`);

describe("gain curves", () => {
  it("converts decibels", () => {
    close(f.dbToGain(0), 1);
    close(f.dbToGain(-20), 0.1);
    close(f.gainToDb(0.5), -6.0206, 1e-4);
    assert.equal(f.gainToDb(0), -Infinity);
    close(f.dbToGain(f.DUCK_DB), 0.316227766, 1e-6);
  });

  it("equal-power crossfade keeps the summed power at 1 throughout", () => {
    const up = { from: 0, to: 1, t0: 10, dur: 2.6, shape: "in" as const };
    const down = { from: 1, to: 0, t0: 10, dur: 2.6, shape: "out" as const };
    for (let t = 9.5; t <= 13; t += 0.05) {
      const a = f.rampValue(up, t);
      const b = f.rampValue(down, t);
      close(a * a + b * b, 1, 1e-9);
    }
  });

  it("starts and ends exactly, and holds outside the ramp", () => {
    const r = { from: 0.2, to: 0.9, t0: 1, dur: 2, shape: "smooth" as const };
    assert.equal(f.rampValue(r, 0), 0.2);
    assert.equal(f.rampValue(r, 1), 0.2);
    assert.equal(f.rampValue(r, 3), 0.9);
    assert.equal(f.rampValue(r, 99), 0.9);
    close(f.rampValue(r, 2), 0.55);
    assert.equal(f.rampValue({ ...r, dur: 0 }, 1), 0.9);
  });

  it("is monotonic and smooth (no step bigger than a segment's share)", () => {
    for (const shape of ["in", "out", "smooth"] as const) {
      const from = shape === "out" ? 1 : 0;
      const to = shape === "out" ? 0 : 1;
      const pts = f.rampPoints({ from, to, t0: 0, dur: 2.6, shape }, 32);
      assert.equal(pts.length, 33);
      assert.equal(pts[0][1], from);
      assert.equal(pts[32][1], to);
      for (let i = 1; i < pts.length; i++) {
        assert.ok((pts[i][1] - pts[i - 1][1]) * (to - from) >= -1e-12, `${shape} monotonic`);
        assert.ok(pts[i][0] > pts[i - 1][0]);
      }
      // A sine-shaped fade's steepest segment is at most pi/2 of the average.
      assert.ok(f.maxStep(pts) <= (Math.PI / 2 / 32) * 1.0001, `${shape} max step ${f.maxStep(pts)}`);
    }
  });

  it("restarts an interrupted fade from where it was (no jump)", () => {
    const first = { from: 0, to: 1, t0: 0, dur: 2.6, shape: "in" as const };
    const at = 1.1;
    const mid = f.rampValue(first, at);
    const second = { from: mid, to: 0, t0: at, dur: 0.35, shape: "out" as const };
    close(f.rampValue(second, at), mid);
    const pts = f.rampPoints(second, 16);
    close(pts[0][1], mid);
  });

  it("ducks down quicker than it comes back", () => {
    assert.ok(f.DUCK_DOWN < f.DUCK_UP);
    assert.ok(f.MUTE_FADE < f.ACTIVE_FADE);
    assert.ok(f.CROSSFADE >= 2 && f.CROSSFADE <= 3);
  });
});

describe("transport", () => {
  type Phase = Parameters<Fades["stepTransport"]>[0];
  const asleep: Phase = { kind: "asleep" };
  const on = { muted: false, active: true, sounding: true };
  const kinds = (a: { kind: string }[]) => a.map((x) => x.kind);

  it("wakes and fades in when there is something to hear", () => {
    const r = f.stepTransport(asleep, on, 5, "era");
    assert.deepEqual(r.phase, { kind: "playing" });
    assert.deepEqual(kinds(r.actions), ["resume", "level"]);
    assert.deepEqual(r.actions[1], { kind: "level", to: 1, dur: f.ACTIVE_FADE });
  });

  it("does nothing while asleep and silent", () => {
    for (const i of [
      { ...on, muted: true },
      { ...on, active: false },
      { ...on, sounding: false },
    ]) {
      const r = f.stepTransport(asleep, i, 1);
      assert.deepEqual(r, { phase: asleep, actions: [] });
    }
  });

  it("mute: quick fade, then suspend once the fade is done", () => {
    const playing: Phase = { kind: "playing" };
    const r = f.stepTransport(playing, { ...on, muted: true }, 10, "mute");
    assert.deepEqual(r.phase, { kind: "stopping", until: 10 + f.MUTE_FADE });
    assert.deepEqual(r.actions, [{ kind: "level", to: 0, dur: f.MUTE_FADE }]);
    // Not yet...
    assert.deepEqual(f.tickTransport(r.phase, 10.1).actions, []);
    // ...now.
    const t = f.tickTransport(r.phase, 10 + f.MUTE_FADE);
    assert.deepEqual(t.phase, asleep);
    assert.deepEqual(kinds(t.actions), ["suspend"]);
  });

  it("unmute: resume, then fade back in", () => {
    const r = f.stepTransport(asleep, on, 20, "mute");
    assert.deepEqual(kinds(r.actions), ["resume", "level"]);
    assert.deepEqual(r.actions[1], { kind: "level", to: 1, dur: f.UNMUTE_FADE });
  });

  it("unmute during the mute fade cancels the suspend (no resume needed)", () => {
    const stopping: Phase = { kind: "stopping", until: 10.3 };
    const r = f.stepTransport(stopping, on, 10.1, "mute");
    assert.deepEqual(r.phase, { kind: "playing" });
    assert.deepEqual(kinds(r.actions), ["level"]);
    assert.deepEqual(f.tickTransport(r.phase, 11).actions, []);
  });

  it("closing the book ebbs slowly; a mute during it finishes sooner", () => {
    const r = f.stepTransport({ kind: "playing" }, { ...on, active: false }, 0, "active");
    assert.deepEqual(r.phase, { kind: "stopping", until: f.ACTIVE_FADE });
    const m = f.stepTransport(r.phase, { ...on, active: false, muted: true }, 0.5, "mute");
    assert.deepEqual(m.phase, { kind: "stopping", until: 0.5 + f.MUTE_FADE });
    assert.deepEqual(m.actions, [{ kind: "level", to: 0, dur: f.MUTE_FADE }]);
    // A slower request never extends a sooner end.
    const again = f.stepTransport(m.phase, { ...on, active: false, muted: false }, 0.6, "active");
    assert.deepEqual(again, { phase: m.phase, actions: [] });
  });

  it("sleeps straight away when the voices have rung out", () => {
    const r = f.stepTransport({ kind: "playing" }, { ...on, sounding: false }, 3);
    assert.deepEqual(r.phase, asleep);
    assert.deepEqual(kinds(r.actions), ["level", "suspend"]);
  });

  it("stays put when nothing changes", () => {
    const playing: Phase = { kind: "playing" };
    assert.deepEqual(f.stepTransport(playing, on, 1), { phase: playing, actions: [] });
    assert.deepEqual(f.tickTransport(playing, 1e9), { phase: playing, actions: [] });
  });

  it("a full sequence never suspends while it should be audible", () => {
    // open -> mute -> unmute quickly -> close -> open
    let phase: Phase = asleep;
    let level = 0;
    let suspended = true;
    const apply = (r: { phase: Phase; actions: { kind: string; to?: number }[] }) => {
      phase = r.phase;
      for (const a of r.actions) {
        if (a.kind === "resume") suspended = false;
        if (a.kind === "suspend") suspended = true;
        if (a.kind === "level") level = a.to!;
      }
    };
    const script: [number, Partial<typeof on>, "mute" | "active" | "era"][] = [
      [0, {}, "active"],
      [5, { muted: true }, "mute"],
      [5.1, { muted: false }, "mute"],
      [8, { active: false }, "active"],
      [12, { active: true }, "active"],
    ];
    let state = { ...on };
    for (const [t, change, why] of script) {
      state = { ...state, ...change };
      apply(f.stepTransport(phase, state, t, why));
      apply(f.tickTransport(phase, t + 0.05));
      if (f.wantsSound(state)) {
        assert.equal(suspended, false, `audible at ${t}`);
        assert.equal(level, 1);
      }
    }
    apply(f.tickTransport(phase, 9.7));
    assert.equal(phase.kind, "playing");
  });
});
