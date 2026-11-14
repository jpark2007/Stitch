/**
 * Paper sounds, synthesised with WebAudio (no audio files).
 *
 *   pageTurn(duration, dir)  a swish of filtered noise that sweeps with the
 *                            page, a few crinkles as it lifts, a soft flap
 *   paperLift()              crinkle when a corner is picked up
 *   pageLand(dir)            the soft flap of a sheet settling
 *   coverOpen()              a card "thock" and a slower swish
 *
 * Each sound is synthesised once, off the main thread, into an AudioBuffer
 * (OfflineAudioContext) when audio starts, in a few variants. Playing one is
 * then a single buffer source with a little rate and pan variation, so a turn
 * costs the main thread almost nothing. Audio starts on the reader's first
 * gesture (`unlockAudio`). Mute state persists per browser.
 */

const STORE_KEY = "stitch:muted";

type SoundName = "turn" | "lift" | "land" | "cover";

let ctx: AudioContext | null = null;
let master: GainNode | null = null;
const bank = new Map<SoundName, AudioBuffer[]>();
let muted = readMuted();
const listeners = new Set<(m: boolean) => void>();

function readMuted(): boolean {
  try {
    return typeof localStorage !== "undefined" && localStorage.getItem(STORE_KEY) === "1";
  } catch {
    return false;
  }
}

export function isMuted() {
  return muted;
}

export function setMuted(m: boolean) {
  muted = m;
  try {
    localStorage.setItem(STORE_KEY, m ? "1" : "0");
  } catch {
    // Private mode: keep it in memory.
  }
  if (master && ctx) master.gain.setTargetAtTime(m ? 0 : 0.9, ctx.currentTime, 0.03);
  // Unmuting is a click: a fine moment to start audio.
  if (!m) unlockAudio();
  listeners.forEach((l) => l(m));
}

export function subscribeMuted(l: (m: boolean) => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

/**
 * Create the AudioContext inside the reader's first gesture (pointerdown or
 * keydown), before any animation starts: constructing one can block the main
 * thread on some systems, and must never land mid-turn.
 */
export function unlockAudio() {
  if (muted || typeof window === "undefined") return;
  if (ctx) {
    if (ctx.state === "suspended") void ctx.resume();
    return;
  }
  const AC = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return;
  ctx = new AC();
  master = ctx.createGain();
  master.gain.value = 0.9;
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -18;
  comp.ratio.value = 3;
  master.connect(comp).connect(ctx.destination);
  // Warm the graph with a silent blip, so the first real sound starts nothing new.
  const blip = ctx.createBufferSource();
  blip.buffer = ctx.createBuffer(1, 128, ctx.sampleRate);
  blip.connect(ctx.createStereoPanner()).connect(master);
  blip.start();
  void renderBank(ctx.sampleRate);
}

/* ------------------------------------------------------------------------ */
/* Offline synthesis                                                         */
/* ------------------------------------------------------------------------ */

let noise: AudioBuffer | null = null;

/** Two seconds of pinkish noise, shared by every voice. */
function noiseBuffer(rate: number): AudioBuffer {
  if (noise && noise.sampleRate === rate) return noise;
  const len = rate * 2;
  noise = new AudioBuffer({ length: len, sampleRate: rate, numberOfChannels: 1 });
  const d = noise.getChannelData(0);
  let b0 = 0;
  let b1 = 0;
  let b2 = 0;
  for (let i = 0; i < len; i++) {
    const w = Math.random() * 2 - 1;
    b0 = 0.99765 * b0 + w * 0.099046;
    b1 = 0.963 * b1 + w * 0.2965164;
    b2 = 0.57 * b2 + w * 1.0526913;
    d[i] = (b0 + b1 + b2 + w * 0.1848) * 0.2;
  }
  return noise;
}

interface Burst {
  type: BiquadFilterType;
  f0: number;
  f1?: number;
  q?: number;
  gain: number;
  attack?: number;
}

/** A filtered, enveloped noise burst into `out`. */
function burst(ac: BaseAudioContext, out: AudioNode, at: number, dur: number, o: Burst) {
  const src = ac.createBufferSource();
  src.buffer = noiseBuffer(ac.sampleRate);
  src.loop = true;
  const filter = ac.createBiquadFilter();
  filter.type = o.type;
  filter.Q.value = o.q ?? 0.8;
  filter.frequency.setValueAtTime(o.f0, at);
  if (o.f1) filter.frequency.exponentialRampToValueAtTime(o.f1, at + dur);
  const g = ac.createGain();
  const attack = o.attack ?? dur * 0.25;
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(o.gain, at + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  src.connect(filter).connect(g).connect(out);
  src.start(at, Math.random() * 1.5);
  src.stop(at + dur + 0.02);
}

function crinkle(ac: BaseAudioContext, out: AudioNode, at: number, count: number, spread: number, gain: number) {
  for (let i = 0; i < count; i++) {
    burst(ac, out, at + Math.random() * spread, 0.012 + Math.random() * 0.025, {
      type: "bandpass",
      f0: 2500 + Math.random() * 4000,
      q: 2.5,
      gain: gain * (0.4 + Math.random() * 0.6),
      attack: 0.002,
    });
  }
}

/** Each recipe draws one variant of a sound starting at t = 0. */
const RECIPES: Record<SoundName, { seconds: number; draw: (ac: BaseAudioContext, out: AudioNode) => void }> = {
  // Written for a 1.15 s turn; faster turns play it a little faster.
  turn: {
    seconds: 1.0,
    draw(ac, out) {
      crinkle(ac, out, 0.005, 5, 0.12, 0.05);
      burst(ac, out, 0.05, 0.72, { type: "bandpass", f0: 700, f1: 2600, q: 0.7, gain: 0.16, attack: 0.34 });
      burst(ac, out, 0.71, 0.12, { type: "lowpass", f0: 900, q: 0.5, gain: 0.1, attack: 0.012 });
    },
  },
  lift: {
    seconds: 0.15,
    draw(ac, out) {
      crinkle(ac, out, 0.005, 4, 0.08, 0.045);
    },
  },
  land: {
    seconds: 0.45,
    draw(ac, out) {
      burst(ac, out, 0.005, 0.32, { type: "bandpass", f0: 1800, f1: 900, q: 0.6, gain: 0.07, attack: 0.08 });
      burst(ac, out, 0.2, 0.1, { type: "lowpass", f0: 800, q: 0.5, gain: 0.08, attack: 0.01 });
    },
  },
  cover: {
    seconds: 1.35,
    draw(ac, out) {
      // Card board leaving the block: a low thock, then a slow swish and a thud.
      const osc = ac.createOscillator();
      const g = ac.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(150, 0.005);
      osc.frequency.exponentialRampToValueAtTime(70, 0.15);
      g.gain.setValueAtTime(0.0001, 0.005);
      g.gain.exponentialRampToValueAtTime(0.16, 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, 0.19);
      osc.connect(g).connect(out);
      osc.start(0.005);
      osc.stop(0.21);
      burst(ac, out, 0.005, 0.06, { type: "lowpass", f0: 1200, q: 0.5, gain: 0.08, attack: 0.004 });
      burst(ac, out, 0.12, 0.9, { type: "bandpass", f0: 500, f1: 1500, q: 0.6, gain: 0.1, attack: 0.35 });
      burst(ac, out, 1.05, 0.16, { type: "lowpass", f0: 500, q: 0.6, gain: 0.14, attack: 0.012 });
    },
  },
};

const VARIANTS: Record<SoundName, number> = { turn: 3, lift: 2, land: 2, cover: 1 };

async function renderBank(rate: number) {
  for (const name of Object.keys(RECIPES) as SoundName[]) {
    const { seconds, draw } = RECIPES[name];
    const list: AudioBuffer[] = [];
    for (let v = 0; v < VARIANTS[name]; v++) {
      try {
        const off = new OfflineAudioContext(1, Math.ceil(seconds * rate), rate);
        draw(off, off.destination);
        list.push(await off.startRendering());
      } catch {
        // No offline rendering: stay silent rather than fail.
      }
    }
    bank.set(name, list);
  }
}

/* ------------------------------------------------------------------------ */
/* Playback                                                                  */
/* ------------------------------------------------------------------------ */

function play(name: SoundName, opts: { rate?: number; pan?: [number, number]; gain?: number } = {}) {
  if (muted || !ctx || !master) return;
  if (ctx.state === "suspended") void ctx.resume();
  const list = bank.get(name);
  if (!list?.length) return;
  const buf = list[Math.floor(Math.random() * list.length)];
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const rate = (opts.rate ?? 1) * (0.96 + Math.random() * 0.08);
  src.playbackRate.value = rate;
  const pan = ctx.createStereoPanner();
  const t = ctx.currentTime;
  const len = buf.duration / rate;
  pan.pan.setValueAtTime(opts.pan?.[0] ?? 0, t);
  if (opts.pan) pan.pan.linearRampToValueAtTime(opts.pan[1], t + len);
  let node: AudioNode = src.connect(pan);
  if (opts.gain !== undefined) {
    const g = ctx.createGain();
    g.gain.value = opts.gain;
    node = node.connect(g);
  }
  node.connect(master);
  src.start(t);
}

/** A full page turn lasting `dur` seconds; `dir` +1 sweeps right to left. */
export function pageTurn(dur = 1.15, dir: 1 | -1 = 1) {
  play("turn", { rate: Math.min(1.5, 1.15 / dur), pan: [0.35 * dir, -0.35 * dir] });
}

export function paperLift() {
  play("lift");
}

export function pageLand(dir: 1 | -1 = 1) {
  play("land", { pan: [0.2 * dir, -0.25 * dir] });
}

export function coverOpen() {
  play("cover", { pan: [0.25, -0.25] });
}
