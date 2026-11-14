/**
 * Sample generators for the music engine, as plain Float32Arrays: the
 * Karplus-Strong string, the vinyl bed, the room's impulse response and a
 * pink-noise loop. Each is seeded, so the same call returns the same samples,
 * and each starts and ends at silence (or loops seamlessly), so playing one
 * never clicks.
 *
 * Pure and free of runtime imports: Node's test runner imports it directly
 * (dsp.test.ts). patches.ts wraps the arrays in AudioBuffers.
 */

/** mulberry32 (the same generator as score.ts, kept local so this file has no imports). */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface StringSpec {
  /** 0..1: how bright the pluck's excitation is (lowpass on the noise burst). */
  brightness: number;
  /** Seconds for the low notes to fall 60 dB; high notes ring shorter. */
  t60: number;
  /** Where along the string it is plucked, 0..0.5 (comb on the excitation). */
  pickPos: number;
  /** Length of the buffer in seconds. */
  seconds: number;
  seed: number;
}

/**
 * A plucked string (Karplus-Strong with an allpass for exact tuning and a
 * per-period loss for the decay time). Fades in over 1.5 ms and out over the
 * last 60 ms; DC removed; peak normalised to 0.7.
 */
export function karplusStrong(freq: number, sampleRate: number, spec: StringSpec): Float32Array {
  const n = Math.max(1, Math.round(spec.seconds * sampleRate));
  const out = new Float32Array(n);
  const period = sampleRate / freq;
  // The two-point average delays half a sample; the allpass takes the fraction.
  const delay = Math.max(2, Math.floor(period - 0.5));
  const frac = period - 0.5 - delay;
  const ap = (1 - frac) / (1 + frac);
  // Shorter ring for higher strings, as on a real guitar.
  const t60 = spec.t60 * Math.pow(110 / Math.max(55, freq), 0.35);
  const loss = Math.pow(10, -3 / (t60 * freq));

  // Excitation: the string's shape when the finger lets go (a triangle peaked
  // at the pick position, zero mean), plus a little lowpassed noise for the
  // pick's grain. A pure noise burst would be far brighter and spikier
  // (~20 dB crest); this keeps a pluck round and its peak near its body.
  const r = rng(spec.seed);
  const pick = Math.max(1, Math.min(delay - 1, Math.round(delay * spec.pickPos)));
  const line = new Float32Array(delay);
  let mean = 0;
  for (let i = 0; i < delay; i++) {
    const tri = i < pick ? i / pick : (delay - i) / (delay - pick);
    line[i] = tri;
    mean += tri;
  }
  mean /= delay;
  const a = 0.15 + 0.8 * spec.brightness;
  let lp = 0;
  for (let i = 0; i < delay; i++) {
    lp += a * (r() * 2 - 1 - lp);
    line[i] = line[i] - mean + spec.brightness * 0.6 * lp;
  }

  let idx = 0;
  let prev = 0;
  let apX = 0;
  let apY = 0;
  for (let i = 0; i < n; i++) {
    const cur = line[idx];
    const avg = 0.5 * (cur + prev);
    prev = cur;
    // First-order allpass: y = ap*x + x1 - ap*y1
    const y = ap * avg + apX - ap * apY;
    apX = avg;
    apY = y;
    line[idx] = y * loss;
    out[i] = cur;
    idx++;
    if (idx === delay) idx = 0;
  }
  removeDc(out);
  fadeEdges(out, Math.round(0.0015 * sampleRate), Math.round(0.06 * sampleRate));
  normalise(out, 0.7);
  return out;
}

/**
 * A stereo room: early reflections then an exponentially decaying, darkening
 * tail, with decorrelated left and right. Energy-normalised so a full send
 * comes back at about the dry level.
 *
 * Made once per AudioContext inside the reader's first gesture, so it is
 * written to be cheap (~3 ms warm, ~7 ms cold, for 2.6 s at 48 kHz): the
 * random generator is inlined, the decay is a running product and the
 * darkening filter's coefficient updates every 64 samples.
 */
export function reverbImpulse(sampleRate: number, seconds: number, seed: number): [Float32Array, Float32Array] {
  const n = Math.round(sampleRate * seconds);
  const pre = Math.round(sampleRate * 0.014);
  const chans: [Float32Array, Float32Array] = [new Float32Array(n), new Float32Array(n)];
  // Amplitude falls 60 dB over `seconds`.
  const step = Math.exp(-6.9 / (seconds * sampleRate));
  const fadeLen = Math.round(sampleRate * 0.006);
  for (let c = 0; c < 2; c++) {
    const ch = chans[c];
    // mulberry32, inlined (this loop is the hot one).
    let s = (seed + c * 7919) >>> 0;
    let lp = 0;
    let env = 1;
    let a = 1;
    for (let i = pre; i < n; i++) {
      const k = i - pre;
      if ((k & 63) === 0) {
        // The tail darkens: the lowpass closes from ~9 kHz to ~1.5 kHz.
        const fc = 1500 + 7500 * Math.exp(-k / sampleRate / 0.35);
        a = 1 - Math.exp((-2 * Math.PI * fc) / sampleRate);
      }
      s = (s + 0x6d2b79f5) | 0;
      let t = Math.imul(s ^ (s >>> 15), 1 | s);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      lp += a * (((t ^ (t >>> 14)) >>> 0) / 2147483648 - 1 - lp);
      ch[i] = lp * env;
      env *= step;
    }
    for (let i = 0; i < fadeLen; i++) ch[pre + i] *= i / fadeLen;
    // A few early reflections.
    const r = rng(seed + c * 104729);
    for (let k = 0; k < 6; k++) {
      const at = pre + Math.round(sampleRate * (0.007 + r() * 0.045));
      if (at < n) ch[at] += (r() < 0.5 ? -1 : 1) * (0.35 - k * 0.04);
    }
    fadeEdges(ch, 0, Math.round(sampleRate * 0.05));
  }
  let energy = 0;
  for (const ch of chans) for (let i = 0; i < n; i++) energy += ch[i] * ch[i];
  const g = 1 / Math.sqrt(Math.max(1e-9, energy / 2));
  for (const ch of chans) for (let i = 0; i < n; i++) ch[i] *= g;
  return chans;
}

/** Pink-ish noise (Paul Kellet's economy filter), seamless when looped, peak 0.9. */
export function pinkNoise(sampleRate: number, seconds: number, seed: number): Float32Array {
  const n = Math.round(sampleRate * seconds);
  const xf = Math.round(sampleRate * 0.05);
  const raw = new Float32Array(n + xf);
  const r = rng(seed);
  let b0 = 0;
  let b1 = 0;
  let b2 = 0;
  for (let i = 0; i < raw.length; i++) {
    const w = r() * 2 - 1;
    b0 = 0.99765 * b0 + w * 0.099046;
    b1 = 0.963 * b1 + w * 0.2965164;
    b2 = 0.57 * b2 + w * 1.0526913;
    raw[i] = (b0 + b1 + b2 + w * 0.1848) * 0.2;
  }
  const out = raw.slice(0, n);
  for (let i = 0; i < xf; i++) {
    const x = i / xf;
    out[i] = out[i] * Math.sin((x * Math.PI) / 2) + raw[n + i] * Math.cos((x * Math.PI) / 2);
  }
  normalise(out, 0.9);
  return out;
}

/* ------------------------------------------------------------------------ */

function removeDc(a: Float32Array) {
  // One-pole highpass at ~20 Hz (sample-rate independent enough for audio rates).
  let x1 = 0;
  let y1 = 0;
  const R = 0.9975;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = x - x1 + R * y1;
    x1 = x;
    y1 = y;
    a[i] = y;
  }
}

function normalise(a: Float32Array, peak: number) {
  let m = 0;
  for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i]));
  if (m > 0) {
    const g = peak / m;
    for (let i = 0; i < a.length; i++) a[i] *= g;
  }
}

/** Raised-cosine fade in over `fin` samples and out over the last `fout`. */
export function fadeEdges(a: Float32Array, fin: number, fout: number) {
  const n = a.length;
  for (let i = 0; i < Math.min(fin, n); i++) a[i] *= 0.5 - 0.5 * Math.cos((Math.PI * i) / fin);
  for (let i = 0; i < Math.min(fout, n); i++) a[n - 1 - i] *= 0.5 - 0.5 * Math.cos((Math.PI * i) / fout);
}
