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

  it("soft ceiling: transparent where the music lives, never above -12 dBFS", () => {
    const curve = f.softClipCurve(2049, f.CEILING);
    close(f.gainToDb(f.CEILING), -12.04, 0.01);
    const at = (x: number) => curve[Math.round(((x + 1) / 2) * 2048)];
    assert.ok(Math.abs(at(0)) < 1e-9);
    for (let i = 1; i < curve.length; i++) assert.ok(curve[i] > curve[i - 1], "monotonic");
    for (const v of curve) assert.ok(Math.abs(v) <= f.CEILING);
    // Around -24 dBFS (the music's peaks are rarely above) it changes the level by under 0.3 dB.
    const x = f.dbToGain(-24);
    assert.ok(Math.abs(f.gainToDb(Math.abs(at(x))) - f.gainToDb(x)) < 0.3);
    // Odd symmetry: no DC from the shaper.
    close(at(0.3), -at(-0.3), 1e-6);
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

describe("voices while the music sleeps", () => {
  type In = Parameters<Fades["wantsSound"]>[0];
  const on: In = { muted: false, active: true, sounding: true };

  it("builds a voice only for music about to be heard", () => {
    assert.equal(f.buildsVoice(on, "1960s", "1970s"), true);
    assert.equal(f.buildsVoice(on, "1960s", "1960s"), false);
    assert.equal(f.buildsVoice({ ...on, muted: true }, "1960s", "1970s"), false);
    assert.equal(f.buildsVoice({ ...on, active: false }, "1960s", "1970s"), false);
  });

  it("frees a sleeping engine's voices only when they hold another era", () => {
    assert.equal(f.dropsVoices({ kind: "asleep" }, "1960s", "1970s"), true);
    assert.equal(f.dropsVoices({ kind: "asleep" }, "1960s", null), true);
    // The same era is kept, so an unmute resumes it where it was.
    assert.equal(f.dropsVoices({ kind: "asleep" }, "1960s", "1960s"), false);
    // Still fading out: the level is not 0 yet.
    assert.equal(f.dropsVoices({ kind: "stopping", until: 9 }, "1960s", "1970s"), false);
    assert.equal(f.dropsVoices({ kind: "playing" }, "1960s", "1970s"), false);
  });

  /**
   * engine.ts's pass in miniature, with voices as a count: each build adds
   * one (a crossfade keeps at most two sounding; ended ones are freed when
   * they ring out), a sleeping engine's drop frees them all.
   */
  function engine() {
    let phase: Parameters<Fades["stepTransport"]>[0] = { kind: "asleep" };
    let held: string | null = null;
    let era: string | null = null;
    let muted = false;
    let active = true;
    let voices = 0;
    let built = 0;
    let now = 0;
    const input = () => ({ muted, active, sounding: era !== null || held !== null });
    const pass = (why?: "mute" | "active" | "era") => {
      if (why) {
        if (f.buildsVoice(input(), held, era)) {
          if (era) {
            voices = Math.min(voices, 1) + 1;
            built++;
          }
          held = era;
        }
        phase = f.stepTransport(phase, input(), now, why).phase;
      }
      if (phase.kind !== "asleep") phase = f.tickTransport(phase, now).phase;
      if (f.dropsVoices(phase, held, era)) {
        held = null;
        voices = 0;
      }
    };
    return {
      setEra(e: string | null) {
        era = e;
        pass("era");
      },
      mute(m: boolean) {
        muted = m;
        pass("mute");
      },
      active(a: boolean) {
        active = a;
        pass("active");
      },
      wait(s: number) {
        now += s;
        pass();
      },
      get voices() {
        return voices;
      },
      get built() {
        return built;
      },
      get phase() {
        return phase.kind;
      },
    };
  }

  it("turning spreads in a muted book builds nothing; the unmute builds one", () => {
    const m = engine();
    m.setEra("1960s");
    m.wait(3);
    assert.equal(m.built, 1);
    m.mute(true);
    m.wait(1);
    assert.equal(m.phase, "asleep");
    for (let i = 0; i < 40; i++) {
      m.setEra(i % 2 ? "1960s" : "1970s");
      assert.ok(m.voices <= 1, `voices ${m.voices} after ${i + 1} changes`);
    }
    assert.equal(m.built, 1);
    m.mute(false);
    assert.equal(m.phase, "playing");
    assert.equal(m.voices, 1);
    // The held 1960s voice went at the first change; the unmute builds one.
    assert.equal(m.built, 2);
  });

  it("an unmute with the era unchanged resumes the held voice", () => {
    const m = engine();
    m.setEra("1960s");
    m.mute(true);
    m.wait(1);
    m.setEra("1960s");
    m.mute(false);
    assert.equal(m.voices, 1);
    assert.equal(m.built, 1);
  });

  it("an era changed while muted is built once, at the unmute", () => {
    const m = engine();
    m.setEra("1960s");
    m.mute(true);
    m.wait(1);
    m.setEra("1970s");
    assert.equal(m.voices, 0);
    m.mute(false);
    assert.equal(m.voices, 1);
    assert.equal(m.built, 2);
  });

  it("the book closed: nothing built while it ebbs or sleeps", () => {
    const m = engine();
    m.setEra("1980s");
    m.active(false);
    for (const e of ["1990s", "2000s", "2010s"]) m.setEra(e);
    assert.equal(m.phase, "stopping");
    assert.equal(m.built, 1);
    m.wait(2);
    assert.equal(m.phase, "asleep");
    assert.equal(m.voices, 0);
    m.active(true);
    assert.equal(m.built, 2);
    assert.equal(m.voices, 1);
  });
});

describe("voice fade stages", () => {
  type Ramp = Parameters<Fades["rampValue"]>[0];
  /** A voice's stages as data, driven like voice.ts drives them. */
  function voice() {
    const v = { in: null as Ramp | null, out: null as Ramp | null, cut: null as Ramp | null, stopAt: Infinity, fired: 0 };
    const at = (r: Ramp | null, rest: number, t: number) => (r ? f.rampValue(r, t) : rest);
    return {
      v,
      start(now: number, dur: number) {
        v.in = { from: 0, to: 1, t0: now, dur, shape: "in" };
      },
      end(now: number, dur: number) {
        const stage = f.planEnd(v.out !== null, v.cut !== null, v.stopAt, now, dur);
        if (!stage) return;
        v[stage] = { from: 1, to: 0, t0: now, dur, shape: "out" };
        v.stopAt = now + dur;
        v.fired++;
      },
      level: (t: number) => at(v.in, 0, t) * at(v.out, 1, t) * at(v.cut, 1, t),
    };
  }

  it("each stage moves once; a later end changes nothing", () => {
    const x = voice();
    x.start(0, f.CROSSFADE);
    x.end(5, f.CROSSFADE);
    assert.equal(f.planEnd(true, false, x.v.stopAt, 6, f.CROSSFADE), null, "later end ignored");
    x.end(6, 0.35);
    assert.equal(x.v.fired, 2);
    assert.equal(x.v.stopAt, 6.35);
    x.end(6.1, 0.1);
    assert.equal(x.v.fired, 2, "both stages used: nothing left to move");
    assert.equal(x.level(6.35), 0);
  });

  it("an interrupted crossfade stays continuous: fade-in cut by a fade-out, then a cut", () => {
    const x = voice();
    x.start(0, f.CROSSFADE);
    x.end(1.0, f.CROSSFADE); // the reader turned on mid fade-in
    x.end(1.5, 0.35); // and again: cut short
    let prev = x.level(0);
    let maxStep = 0;
    for (let t = 0.001; t <= 3; t += 0.001) {
      const l = x.level(t);
      maxStep = Math.max(maxStep, Math.abs(l - prev));
      prev = l;
    }
    // A 1 ms step of a 0.35 s cosine is at most ~0.0045; no jumps anywhere.
    assert.ok(maxStep < 0.006, `max step ${maxStep}`);
    assert.equal(x.level(1.85), 0);
    close(x.level(1.0), f.rampValue(x.v.in!, 1.0), 1e-12);
  });

  it("a full crossfade between two voices keeps equal power", () => {
    const a = voice();
    const b = voice();
    a.start(-10, 0.001);
    a.end(0, f.CROSSFADE);
    b.start(0, f.CROSSFADE);
    for (let t = 0; t <= f.CROSSFADE; t += 0.05) close(a.level(t) ** 2 + b.level(t) ** 2, 1, 1e-9);
  });
});
