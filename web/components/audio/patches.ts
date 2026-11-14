/**
 * The band: small instrument patches built from oscillators, FM, filtered
 * noise and Karplus-Strong strings, plus the effects a part's bus can carry.
 *
 * Every note is a handful of nodes scheduled at an exact AudioContext time,
 * with an attack ramp from silence and a release that reaches silence before
 * the sources stop, so notes never click. Nodes only connect downstream (no
 * LFO reaches into a note), so a finished note is garbage the moment it ends;
 * `onended` disconnects it anyway.
 *
 * Works on any BaseAudioContext, so the offline render (offline.ts) plays the
 * same patches as the live engine.
 */

import { karplusStrong, pinkNoise, type StringSpec } from "./dsp";
import { dbToGain } from "./fades";
import type { PartFx } from "./score";

export interface Kit {
  ctx: BaseAudioContext;
  noise: AudioBuffer;
  /** A Karplus-Strong string, cached per patch and note. */
  string(name: string, midi: number): AudioBuffer;
  /** True once `string(name, midi)` would not have to build a buffer. */
  hasString(name: string, midi: number): boolean;
  wave(name: "piano" | "pulse"): PeriodicWave;
}

/** Patches that play Karplus-Strong buffers (the voice warms these ahead). */
export const STRING_PATCHES = new Set(["guitar", "eguitar", "upright"]);

// Buffers are only as long as the longest note needs (held + release).
const STRINGS: Record<string, StringSpec> = {
  guitar: { brightness: 0.45, t60: 2.4, pickPos: 0.18, seconds: 1.7, seed: 11 },
  eguitar: { brightness: 0.4, t60: 1.5, pickPos: 0.12, seconds: 1.3, seed: 23 },
  upright: { brightness: 0.2, t60: 1.4, pickPos: 0.3, seconds: 1.1, seed: 37 },
};

const MAX_STRINGS = 96;

/** One kit per AudioContext: shared noise, waves and string buffers. */
export function createKit(ctx: BaseAudioContext): Kit {
  const rate = ctx.sampleRate;
  const toBuffer = (data: Float32Array) => {
    const b = ctx.createBuffer(1, data.length, rate);
    b.copyToChannel(data as Float32Array<ArrayBuffer>, 0);
    return b;
  };
  const noise = toBuffer(pinkNoise(rate, 2, 5));
  const strings = new Map<string, AudioBuffer>();
  const waves = new Map<string, PeriodicWave>();
  return {
    ctx,
    noise,
    hasString(name, midi) {
      return strings.has(`${name}:${midi}`);
    },
    string(name, midi) {
      const key = `${name}:${midi}`;
      let b = strings.get(key);
      if (b) {
        // Refresh for least-recently-used eviction.
        strings.delete(key);
        strings.set(key, b);
        return b;
      }
      const spec = STRINGS[name] ?? STRINGS.guitar;
      b = toBuffer(karplusStrong(mtof(midi), rate, { ...spec, seed: spec.seed + midi }));
      strings.set(key, b);
      if (strings.size > MAX_STRINGS) strings.delete(strings.keys().next().value!);
      return b;
    },
    wave(name) {
      let w = waves.get(name);
      if (!w) {
        // piano: falling harmonics; pulse: a soft, rounded square.
        const amps = name === "piano" ? [0, 1, 0.42, 0.2, 0.11, 0.05, 0.025, 0.012] : [0, 1, 0, 0.3, 0, 0.14, 0, 0.06, 0, 0.025];
        const real = new Float32Array(amps.length);
        const imag = new Float32Array(amps);
        w = ctx.createPeriodicWave(real, imag);
        waves.set(name, w);
      }
      return w;
    },
  };
}

export const mtof = (m: number) => 440 * Math.pow(2, (m - 69) / 12);

/* ------------------------------------------------------------------------ */
/* Note building blocks                                                      */
/* ------------------------------------------------------------------------ */

export interface NoteArgs {
  t: number;
  dur: number;
  midi: number;
  vel: number;
}

type Patch = (k: Kit, out: AudioNode, n: NoteArgs) => void;

/** Free a note's nodes once its last source ends. */
function release(src: AudioScheduledSourceNode, nodes: AudioNode[]) {
  src.onended = () => {
    for (const n of nodes) n.disconnect();
  };
}

/**
 * An amplitude envelope: linear attack from silence, exponential decay toward
 * `sustain` (a fraction of peak), and an exponential release after the note's
 * held length. Returns when the note is inaudible (for stop()).
 */
function env(
  g: GainNode,
  t: number,
  peak: number,
  attack: number,
  decayTc: number,
  sustain: number,
  dur: number,
  releaseTc: number,
): number {
  const p = g.gain;
  p.setValueAtTime(0, t);
  p.linearRampToValueAtTime(peak, t + attack);
  p.setTargetAtTime(peak * sustain, t + attack, decayTc);
  const off = t + Math.max(dur, attack + 0.005);
  p.setTargetAtTime(0, off, releaseTc);
  return off + releaseTc * 7;
}

function osc(k: Kit, type: OscillatorType | PeriodicWave, freq: number, t: number, detune = 0): OscillatorNode {
  const o = k.ctx.createOscillator();
  if (typeof type === "string") o.type = type as OscillatorType;
  else o.setPeriodicWave(type);
  o.frequency.setValueAtTime(freq, t);
  if (detune) o.detune.setValueAtTime(detune, t);
  return o;
}

/** Sine partials with their own decays into one envelope-less sum. */
function partials(k: Kit, out: AudioNode, n: NoteArgs, list: [ratio: number, amp: number, tc: number][], peak: number, releaseTc: number) {
  const f = mtof(n.midi);
  const nodes: AudioNode[] = [];
  let end = n.t;
  let last: OscillatorNode | null = null;
  for (const [ratio, amp, tc] of list) {
    const fr = f * ratio;
    if (fr > 9000) continue;
    const o = osc(k, "sine", fr, n.t);
    const g = k.ctx.createGain();
    const stop = env(g, n.t, peak * amp * n.vel, 0.003, tc, 0, n.dur, Math.min(releaseTc, tc));
    o.connect(g).connect(out);
    o.start(n.t);
    o.stop(stop);
    nodes.push(o, g);
    if (stop > end) {
      end = stop;
      last = o;
    }
  }
  if (last) release(last, nodes);
}

/** A filtered, enveloped burst of the shared noise. */
function noiseHit(
  k: Kit,
  out: AudioNode,
  t: number,
  o: { type: BiquadFilterType; f0: number; f1?: number; q?: number; peak: number; attack: number; tc: number; len?: number },
) {
  const src = k.ctx.createBufferSource();
  src.buffer = k.noise;
  src.loop = true;
  const f = k.ctx.createBiquadFilter();
  f.type = o.type;
  f.Q.value = o.q ?? 0.7;
  f.frequency.setValueAtTime(o.f0, t);
  if (o.f1) f.frequency.exponentialRampToValueAtTime(o.f1, t + (o.len ?? o.tc * 4));
  const g = k.ctx.createGain();
  const stop = env(g, t, o.peak, o.attack, o.tc, 0, o.len ?? o.attack, o.tc);
  src.connect(f).connect(g).connect(out);
  // Start somewhere different in the loop each hit, deterministically by time.
  src.start(t, (t * 7.31) % 1.5);
  src.stop(stop);
  release(src, [src, f, g]);
}

/* ------------------------------------------------------------------------ */
/* Instruments                                                               */
/* ------------------------------------------------------------------------ */

const PATCHES: Record<string, Patch> = {
  // A comb tooth: fundamental with a quick octave and a faint, fast tine.
  musicbox(k, out, n) {
    partials(k, out, n, [[1, 1, 0.9], [2, 0.22, 0.25], [4.02, 0.05, 0.05]], 0.32, 0.6);
  },

  // Glockenspiel bar: inharmonic partials of a free bar.
  glock(k, out, n) {
    partials(k, out, n, [[1, 1, 0.8], [2.76, 0.12, 0.12], [5.4, 0.03, 0.04]], 0.3, 0.5);
  },

  // Vibraphone: round bar, slow decay, motor tremolo on the part.
  vibes(k, out, n) {
    partials(k, out, n, [[1, 1, 1.3], [3.98, 0.08, 0.1]], 0.32, 0.35);
  },

  // Felted parlour piano: falling harmonics, a closing lowpass, a slow decay.
  piano(k, out, n) {
    pianoNote(k, out, n, { bright: 1, decay: 1, detune: 4 });
  },

  // Lo-fi: duller, a touch more detuned, and shorter.
  lofipiano(k, out, n) {
    pianoNote(k, out, n, { bright: 0.6, decay: 0.85, detune: 7 });
  },

  // Rhodes-like: FM with a decaying index (the bark softens to a bell-pure tone).
  epiano(k, out, n) {
    const f = mtof(n.midi);
    const t = n.t;
    const car = osc(k, "sine", f, t);
    const mod = osc(k, "sine", f, t);
    const idx = k.ctx.createGain();
    idx.gain.setValueAtTime(0, t);
    idx.gain.linearRampToValueAtTime(f * (0.9 + 0.8 * n.vel), t + 0.003);
    idx.gain.setTargetAtTime(f * 0.12, t + 0.003, 0.22);
    mod.connect(idx).connect(car.frequency);
    const g = k.ctx.createGain();
    const stop = env(g, t, 0.3 * n.vel, 0.004, 1.5, 0, n.dur, 0.25);
    car.connect(g).connect(out);
    car.start(t);
    mod.start(t);
    car.stop(stop);
    mod.stop(stop);
    release(car, [car, mod, idx, g]);
  },

  // Plucked strings: precomputed Karplus-Strong buffers, through a body
  // lowpass that softens the pick with the velocity.
  guitar(k, out, n) {
    stringNote(k, out, n, "guitar", 0.5, 0.12, 2400 + 1600 * n.vel);
  },
  eguitar(k, out, n) {
    stringNote(k, out, n, "eguitar", 0.45, 0.1, 2200 + 1200 * n.vel);
  },
  upright(k, out, n) {
    stringNote(k, out, n, "upright", 0.7, 0.08, 900);
  },

  // Round fingered bass: sine with a little triangle, lowpassed.
  bass(k, out, n) {
    const f = mtof(n.midi);
    const a = osc(k, "sine", f, n.t);
    const b = osc(k, "triangle", f, n.t);
    const bg = k.ctx.createGain();
    bg.gain.value = 0.35;
    const lp = k.ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 520;
    lp.Q.value = 0.5;
    const g = k.ctx.createGain();
    const stop = env(g, n.t, 0.42 * n.vel, 0.012, 0.35, 0.45, n.dur, 0.08);
    a.connect(lp);
    b.connect(bg).connect(lp);
    lp.connect(g).connect(out);
    a.start(n.t);
    b.start(n.t);
    a.stop(stop);
    b.stop(stop);
    release(a, [a, b, bg, lp, g]);
  },

  // Sub: a pure sine with a soft attack.
  subbass(k, out, n) {
    const o = osc(k, "sine", mtof(n.midi), n.t);
    const g = k.ctx.createGain();
    const stop = env(g, n.t, 0.45 * n.vel, 0.05, 0.6, 0.6, n.dur, 0.25);
    o.connect(g).connect(out);
    o.start(n.t);
    o.stop(stop);
    release(o, [o, g]);
  },

  // Analog bass: saw through a lowpass with a short filter envelope.
  synthbass(k, out, n) {
    const o = osc(k, "sawtooth", mtof(n.midi), n.t);
    const lp = k.ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.Q.value = 1.2;
    lp.frequency.setValueAtTime(850, n.t);
    lp.frequency.setTargetAtTime(240, n.t, 0.09);
    const g = k.ctx.createGain();
    const stop = env(g, n.t, 0.3 * n.vel, 0.006, 0.2, 0.6, n.dur, 0.06);
    o.connect(lp).connect(g).connect(out);
    o.start(n.t);
    o.stop(stop);
    release(o, [o, lp, g]);
  },

  // 80s pad: three detuned saws, a slow swell; the part's lowpass breathes.
  pad(k, out, n) {
    padNote(k, out, n, ["sawtooth", "sawtooth", "sawtooth"], [-9, 0, 8], 0.11, 1.1, 0.9, 1400);
  },

  // Soft modern pad: triangle and sine an octave apart, slower still.
  warmpad(k, out, n) {
    padNote(k, out, n, ["triangle", "triangle", "sine"], [-6, 5, 1200], 0.17, 1.6, 1.2, 2200);
  },

  // Doo-wop "oh": two detuned saws through vowel formants.
  oohs(k, out, n) {
    const f = mtof(n.midi);
    const t = n.t;
    const a = osc(k, "sawtooth", f, t, -6);
    const b = osc(k, "sawtooth", f, t, 6);
    const mix = k.ctx.createGain();
    mix.gain.value = 0.5;
    const f1 = k.ctx.createBiquadFilter();
    f1.type = "bandpass";
    f1.frequency.value = 440;
    f1.Q.value = 4;
    const f2 = k.ctx.createBiquadFilter();
    f2.type = "bandpass";
    f2.frequency.value = 820;
    f2.Q.value = 6;
    const f2g = k.ctx.createGain();
    f2g.gain.value = 0.5;
    const g = k.ctx.createGain();
    const stop = env(g, t, 0.5 * n.vel, 0.38, 1.2, 0.8, n.dur, 0.45);
    a.connect(mix);
    b.connect(mix);
    mix.connect(f1).connect(g);
    mix.connect(f2).connect(f2g).connect(g);
    g.connect(out);
    a.start(t);
    b.start(t);
    a.stop(stop);
    b.stop(stop);
    release(a, [a, b, mix, f1, f2, f2g, g]);
  },

  // 80s arpeggio: rounded pulse with a quick filter blip.
  arp(k, out, n) {
    const o = osc(k, k.wave("pulse"), mtof(n.midi), n.t);
    const lp = k.ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.Q.value = 2;
    lp.frequency.setValueAtTime(2400, n.t);
    lp.frequency.setTargetAtTime(650, n.t, 0.11);
    const g = k.ctx.createGain();
    const stop = env(g, n.t, 0.22 * n.vel, 0.004, 0.16, 0.25, n.dur, 0.08);
    o.connect(lp).connect(g).connect(out);
    o.start(n.t);
    o.stop(stop);
    release(o, [o, lp, g]);
  },

  // Modern soft pluck: triangle plus octave sine, closing filter.
  pluck(k, out, n) {
    const f = mtof(n.midi);
    const a = osc(k, "triangle", f, n.t);
    const b = osc(k, "sine", f * 2, n.t);
    const bg = k.ctx.createGain();
    bg.gain.value = 0.25;
    const lp = k.ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(2600, n.t);
    lp.frequency.setTargetAtTime(700, n.t, 0.14);
    const g = k.ctx.createGain();
    const stop = env(g, n.t, 0.3 * n.vel, 0.005, 0.32, 0, n.dur, 0.2);
    a.connect(lp);
    b.connect(bg).connect(lp);
    lp.connect(g).connect(out);
    a.start(n.t);
    b.start(n.t);
    a.stop(stop);
    b.stop(stop);
    release(a, [a, b, bg, lp, g]);
  },

  /* ---- drums: soft, dark, never splashy ---- */

  kick(k, out, n) {
    const o = osc(k, "sine", 110, n.t);
    o.frequency.exponentialRampToValueAtTime(46, n.t + 0.12);
    const g = k.ctx.createGain();
    const stop = env(g, n.t, 0.55 * n.vel, 0.004, 0.11, 0, 0.01, 0.11);
    o.connect(g).connect(out);
    o.start(n.t);
    o.stop(stop);
    release(o, [o, g]);
  },

  snare(k, out, n) {
    noiseHit(k, out, n.t, { type: "bandpass", f0: 1700, q: 0.8, peak: 0.32 * n.vel, attack: 0.003, tc: 0.07 });
    const o = osc(k, "triangle", 185, n.t);
    const g = k.ctx.createGain();
    const stop = env(g, n.t, 0.16 * n.vel, 0.002, 0.04, 0, 0.01, 0.04);
    o.connect(g).connect(out);
    o.start(n.t);
    o.stop(stop);
    release(o, [o, g]);
  },

  rim(k, out, n) {
    noiseHit(k, out, n.t, { type: "bandpass", f0: 2200, q: 2.2, peak: 0.16 * n.vel, attack: 0.001, tc: 0.018 });
    const o = osc(k, "triangle", 760, n.t);
    const g = k.ctx.createGain();
    const stop = env(g, n.t, 0.12 * n.vel, 0.001, 0.022, 0, 0.005, 0.022);
    o.connect(g).connect(out);
    o.start(n.t);
    o.stop(stop);
    release(o, [o, g]);
  },

  hat(k, out, n) {
    noiseHit(k, out, n.t, { type: "bandpass", f0: 6200, q: 0.9, peak: 0.22 * n.vel, attack: 0.002, tc: 0.022 });
  },

  shaker(k, out, n) {
    noiseHit(k, out, n.t, { type: "bandpass", f0: 4800, q: 1.1, peak: 0.2 * n.vel, attack: 0.018, tc: 0.035, len: 0.03 });
  },

  // A brush swept round the head: a slow swish, falling in pitch.
  brush(k, out, n) {
    noiseHit(k, out, n.t, { type: "bandpass", f0: 3200, f1: 1600, q: 0.6, peak: 0.18 * n.vel, attack: n.dur * 0.35, tc: n.dur * 0.25, len: n.dur * 0.5 });
  },

  brushtap(k, out, n) {
    noiseHit(k, out, n.t, { type: "bandpass", f0: 2600, q: 0.8, peak: 0.24 * n.vel, attack: 0.002, tc: 0.06 });
  },

  // A speck of dust under the needle: a tiny, damped tick at a varying pitch.
  crackle(k, out, n) {
    const f0 = 1800 + ((n.t * 9173.13) % 1) * 2400;
    noiseHit(k, out, n.t, { type: "bandpass", f0, q: 1.4, peak: 0.5 * n.vel * n.vel, attack: 0.0006, tc: 0.0028 });
  },
};

export function hasPatch(name: string) {
  return name in PATCHES || name === "vinyl";
}

export function playNote(k: Kit, inst: string, out: AudioNode, n: NoteArgs) {
  const p = PATCHES[inst];
  if (p) p(k, out, n);
}

function pianoNote(k: Kit, out: AudioNode, n: NoteArgs, o: { bright: number; decay: number; detune: number }) {
  const f = mtof(n.midi);
  const t = n.t;
  const w = k.wave("piano");
  const a = osc(k, w, f, t, -o.detune / 2);
  const b = osc(k, w, f, t, o.detune / 2);
  const lp = k.ctx.createBiquadFilter();
  lp.type = "lowpass";
  lp.Q.value = 0.4;
  const open = Math.min(5200, f * 7) * o.bright * (0.6 + 0.5 * n.vel);
  lp.frequency.setValueAtTime(open, t);
  lp.frequency.setTargetAtTime(Math.max(f * 1.6, 300), t, 0.45);
  const g = k.ctx.createGain();
  // Lower notes ring longer.
  const tc = (1.7 - Math.min(1, (n.midi - 40) / 50)) * o.decay;
  const stop = env(g, t, 0.17 * n.vel, 0.005, tc, 0, n.dur, 0.2);
  a.connect(lp);
  b.connect(lp);
  lp.connect(g).connect(out);
  a.start(t);
  b.start(t);
  a.stop(stop);
  b.stop(stop);
  release(a, [a, b, lp, g]);
}

function stringNote(k: Kit, out: AudioNode, n: NoteArgs, name: string, peak: number, releaseTc: number, lpFreq?: number) {
  const buf = k.string(name, n.midi);
  const src = k.ctx.createBufferSource();
  src.buffer = buf;
  const g = k.ctx.createGain();
  const p = g.gain;
  const t = n.t;
  p.setValueAtTime(peak * n.vel, t);
  const off = t + Math.max(0.02, n.dur);
  p.setTargetAtTime(0, off, releaseTc);
  const end = Math.min(t + buf.duration, off + releaseTc * 7);
  let head: AudioNode = src;
  const nodes: AudioNode[] = [src, g];
  if (lpFreq) {
    const lp = k.ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = lpFreq;
    lp.Q.value = 0.5;
    head = src.connect(lp);
    nodes.push(lp);
  }
  head.connect(g).connect(out);
  src.start(t);
  // The buffer fades out on its own; an early stop happens only after the release.
  if (end < t + buf.duration) src.stop(end);
  release(src, nodes);
}

function padNote(
  k: Kit,
  out: AudioNode,
  n: NoteArgs,
  types: OscillatorType[],
  detunes: number[],
  peak: number,
  attack: number,
  releaseTc: number,
  cutoff: number,
) {
  const f = mtof(n.midi);
  const t = n.t;
  const lp = k.ctx.createBiquadFilter();
  lp.type = "lowpass";
  lp.Q.value = 0.6;
  lp.frequency.setValueAtTime(cutoff * 0.45, t);
  lp.frequency.linearRampToValueAtTime(cutoff, t + attack);
  const g = k.ctx.createGain();
  const stop = env(g, t, peak * n.vel, attack, 2, 0.85, n.dur, releaseTc);
  const nodes: AudioNode[] = [lp, g];
  let first: OscillatorNode | null = null;
  types.forEach((type, i) => {
    const o = osc(k, type, f, t, detunes[i]);
    o.connect(lp);
    o.start(t);
    o.stop(stop);
    nodes.push(o);
    first ??= o;
  });
  lp.connect(g).connect(out);
  if (first) release(first, nodes);
}

/* ------------------------------------------------------------------------ */
/* Part buses                                                                */
/* ------------------------------------------------------------------------ */

export interface PartBus {
  /** Notes connect here. */
  input: AudioNode;
  /** Stop LFOs and loops at `t` (for the voice's end). */
  stop(t: number): void;
  disconnect(): void;
}

/**
 * A part's bus: input -> effects -> level/pan -> the voice's dry and reverb
 * sends. LFOs here modulate the bus, never a note, so notes stay leaf nodes.
 */
export function createPartBus(
  k: Kit,
  o: { fx?: PartFx[]; gainDb: number; pan?: number; send?: number; beatDur: number; t: number },
  dry: AudioNode,
  wet: AudioNode,
): PartBus {
  const ctx = k.ctx;
  const nodes: AudioNode[] = [];
  const sources: AudioScheduledSourceNode[] = [];
  const mk = <T extends AudioNode>(n: T) => {
    nodes.push(n);
    return n;
  };
  const lfo = (rate: number, depth: number, target: AudioParam, type: OscillatorType = "sine") => {
    const l = mk(ctx.createOscillator());
    l.type = type;
    l.frequency.value = rate;
    const d = mk(ctx.createGain());
    d.gain.value = depth;
    l.connect(d).connect(target);
    l.start(o.t);
    sources.push(l);
  };

  const input = mk(ctx.createGain());
  let head: AudioNode = input;
  for (const fx of o.fx ?? []) {
    switch (fx.kind) {
      case "lowpass":
      case "highpass": {
        const f = mk(ctx.createBiquadFilter());
        f.type = fx.kind;
        f.frequency.value = fx.freq;
        f.Q.value = fx.kind === "lowpass" ? (fx.q ?? 0.7) : 0.7;
        if (fx.kind === "lowpass" && fx.lfoRate && fx.lfoDepth) lfo(fx.lfoRate, fx.lfoDepth, f.frequency);
        head = head.connect(f);
        break;
      }
      case "tremolo": {
        const g = mk(ctx.createGain());
        g.gain.value = 1 - fx.depth / 2;
        lfo(fx.rate, fx.depth / 2, g.gain);
        head = head.connect(g);
        break;
      }
      case "autopan": {
        const p = mk(ctx.createStereoPanner());
        lfo(fx.rate, fx.depth, p.pan);
        head = head.connect(p);
        break;
      }
      case "wobble": {
        // Tape wow: a modulated delay, fully wet, bends the pitch a few cents.
        const d = mk(ctx.createDelay(0.1));
        d.delayTime.value = fx.depth * 2 + 0.002;
        lfo(fx.rate, fx.depth, d.delayTime);
        head = head.connect(d);
        break;
      }
      case "chorus": {
        const out = mk(ctx.createGain());
        const d = mk(ctx.createDelay(0.1));
        d.delayTime.value = fx.delay;
        lfo(fx.rate, fx.depth, d.delayTime);
        const wetG = mk(ctx.createGain());
        wetG.gain.value = fx.mix;
        const dryG = mk(ctx.createGain());
        dryG.gain.value = 1 - fx.mix * 0.5;
        head.connect(dryG).connect(out);
        head.connect(d).connect(wetG).connect(out);
        head = out;
        break;
      }
      case "echo": {
        const out = mk(ctx.createGain());
        const d = mk(ctx.createDelay(4));
        d.delayTime.value = fx.beats * o.beatDur;
        const fb = mk(ctx.createGain());
        fb.gain.value = fx.feedback;
        const tone = mk(ctx.createBiquadFilter());
        tone.type = "lowpass";
        tone.frequency.value = fx.tone;
        const wetG = mk(ctx.createGain());
        wetG.gain.value = fx.mix;
        head.connect(out);
        head.connect(d);
        d.connect(tone).connect(fb).connect(d);
        tone.connect(wetG).connect(out);
        head = out;
        break;
      }
    }
  }
  const level = mk(ctx.createGain());
  level.gain.value = dbToGain(o.gainDb);
  head = head.connect(level);
  if (o.pan) {
    const p = mk(ctx.createStereoPanner());
    p.pan.value = o.pan;
    head = head.connect(p);
  }
  head.connect(dry);
  if (o.send) {
    const s = mk(ctx.createGain());
    s.gain.value = o.send;
    head.connect(s).connect(wet);
  }
  return {
    input,
    stop(t) {
      for (const s of sources) {
        try {
          s.stop(t);
        } catch {
          // Already stopped.
        }
      }
    },
    disconnect() {
      for (const n of nodes) n.disconnect();
    },
  };
}

/**
 * A texture for the life of a voice. "vinyl" is the record's surface hiss:
 * the shared pink-noise loop, band-limited (its crackles are a "crackle"
 * drum part, so they are scheduled like notes and cost no buffer).
 */
export function startBed(k: Kit, inst: string, out: AudioNode, t: number): { stop(t: number): void; disconnect(): void } | null {
  if (inst !== "vinyl") return null;
  const src = k.ctx.createBufferSource();
  src.buffer = k.noise;
  src.loop = true;
  const hp = k.ctx.createBiquadFilter();
  hp.type = "highpass";
  hp.frequency.value = 900;
  hp.Q.value = 0.5;
  const lp = k.ctx.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.value = 4800;
  lp.Q.value = 0.5;
  src.connect(hp).connect(lp).connect(out);
  src.start(t, 0.37);
  return {
    stop(at) {
      try {
        src.stop(at);
      } catch {
        // Already stopped.
      }
    },
    disconnect() {
      src.disconnect();
      hp.disconnect();
      lp.disconnect();
    },
  };
}
