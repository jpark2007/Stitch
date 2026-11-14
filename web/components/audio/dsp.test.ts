/**
 * Sample generators (dsp.ts): string tuning, click-free edges, seamless
 * loops, determinism.
 *
 *   cd web && node --no-warnings --test "components/audio/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Dsp = typeof import("./dsp");
const path = "./dsp.ts";
const d: Dsp = await import(path);

const SR = 44100;
const spec = { brightness: 0.55, t60: 2.6, pickPos: 0.18, seconds: 1.5, seed: 11 };

/** Fundamental by autocorrelation over a window, in Hz. */
function pitch(x: Float32Array, from: number, len: number, minHz = 50, maxHz = 1500): number {
  let best = 0;
  let bestLag = 0;
  for (let lag = Math.floor(SR / maxHz); lag <= Math.ceil(SR / minHz); lag++) {
    let s = 0;
    for (let i = 0; i < len; i++) s += x[from + i] * x[from + i + lag];
    if (s > best) {
      best = s;
      bestLag = lag;
    }
  }
  // Parabolic refinement.
  const ac = (lag: number) => {
    let s = 0;
    for (let i = 0; i < len; i++) s += x[from + i] * x[from + i + lag];
    return s;
  };
  const a = ac(bestLag - 1);
  const b = ac(bestLag);
  const c = ac(bestLag + 1);
  const shift = (a - c) / (2 * (a - 2 * b + c));
  return SR / (bestLag + shift);
}

const peak = (x: Float32Array, from = 0, to = x.length) => {
  let m = 0;
  for (let i = from; i < to; i++) m = Math.max(m, Math.abs(x[i]));
  return m;
};

describe("Karplus-Strong string", () => {
  it("is in tune (within 5 cents) across the guitar's range", () => {
    for (const midi of [40, 47, 55, 62, 69, 76]) {
      const f = 440 * Math.pow(2, (midi - 69) / 12);
      const x = d.karplusStrong(f, SR, spec);
      const got = pitch(x, Math.round(SR * 0.15), 2048);
      const cents = 1200 * Math.log2(got / f);
      assert.ok(Math.abs(cents) < 5, `midi ${midi}: ${got.toFixed(2)} Hz vs ${f.toFixed(2)} (${cents.toFixed(1)} c)`);
    }
  });

  it("starts and ends at silence and peaks at 0.7", () => {
    const x = d.karplusStrong(196, SR, spec);
    assert.ok(Math.abs(x[0]) < 1e-12);
    assert.ok(Math.abs(x[x.length - 1]) < 1e-6);
    assert.ok(Math.abs(peak(x) - 0.7) < 1e-6);
    // The first samples ramp, no instant full-scale step.
    assert.ok(Math.abs(x[1] - x[0]) < 0.05);
  });

  it("decays", () => {
    const x = d.karplusStrong(110, SR, { ...spec, seconds: 2.4 });
    const early = peak(x, 0, SR * 0.2);
    const late = peak(x, SR * 2, SR * 2.2);
    assert.ok(late < early * 0.25, `late ${late} early ${early}`);
  });

  it("is deterministic per seed", () => {
    assert.deepEqual(d.karplusStrong(220, SR, spec), d.karplusStrong(220, SR, spec));
    assert.notDeepEqual(d.karplusStrong(220, SR, spec), d.karplusStrong(220, SR, { ...spec, seed: 12 }));
  });
});

describe("loops", () => {
  for (const [name, make] of [
    ["vinyl", () => d.vinylLoop(SR, 6, 9)],
    ["pink noise", () => d.pinkNoise(SR, 2, 5)],
  ] as const) {
    it(`${name} wraps without a jump bigger than its own sample-to-sample steps`, () => {
      const x = make();
      let maxStep = 0;
      for (let i = 1; i < x.length; i++) maxStep = Math.max(maxStep, Math.abs(x[i] - x[i - 1]));
      const seam = Math.abs(x[0] - x[x.length - 1]);
      assert.ok(seam <= maxStep, `seam ${seam} vs max step ${maxStep}`);
    });
  }

  it("vinyl is quiet: crackles stay soft", () => {
    const x = d.vinylLoop(SR, 6, 9);
    assert.ok(peak(x) < 0.6, `peak ${peak(x)}`);
  });
});

describe("room impulse", () => {
  it("is stereo, decorrelated, and decays to silence", () => {
    const [l, r] = d.reverbImpulse(SR, 2.6, 3);
    assert.equal(l.length, Math.round(SR * 2.6));
    assert.equal(l[0], 0, "pre-delay");
    let dot = 0;
    let el = 0;
    let er = 0;
    for (let i = 0; i < l.length; i++) {
      dot += l[i] * r[i];
      el += l[i] * l[i];
      er += r[i] * r[i];
    }
    assert.ok(Math.abs(dot / Math.sqrt(el * er)) < 0.2, "decorrelated");
    assert.ok(peak(l, l.length - 200) < 1e-3 * peak(l), "tail at silence");
  });
});
