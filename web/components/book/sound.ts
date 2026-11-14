/**
 * Paper sounds, synthesised with WebAudio (no audio files).
 *
 *   pageTurn(duration, pan)  a swish of filtered noise that sweeps with the
 *                            page, a few crinkles as it lifts, a soft flap
 *   paperLift()              crinkle when a corner is picked up
 *   pageLand()               the soft flap of a sheet settling
 *   coverOpen()              a card "thock" and a slower swish
 *
 * The AudioContext is created lazily on the first sound (always after a user
 * gesture). Mute state persists per browser.
 */

const STORE_KEY = "stitch:muted";

let ctx: AudioContext | null = null;
let master: GainNode | null = null;
let noise: AudioBuffer | null = null;
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
  if (!m) audio();
  listeners.forEach((l) => l(m));
}

export function subscribeMuted(l: (m: boolean) => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

function audio(): AudioContext | null {
  if (muted) return null;
  if (typeof window === "undefined") return null;
  if (!ctx) {
    const AC = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    master = ctx.createGain();
    master.gain.value = 0.9;
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -18;
    comp.ratio.value = 3;
    master.connect(comp).connect(ctx.destination);
    // Two seconds of pinkish noise, reused by every sound.
    const len = ctx.sampleRate * 2;
    noise = ctx.createBuffer(1, len, ctx.sampleRate);
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
  }
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

/**
 * Create the AudioContext inside the reader's first gesture (pointerdown or
 * keydown), before any animation starts: constructing one can block the main
 * thread for a while on some systems, and must not land mid-turn.
 */
export function unlockAudio() {
  if (!muted) audio();
}

function noiseSource(ac: AudioContext, offset = Math.random() * 1.5) {
  const src = ac.createBufferSource();
  src.buffer = noise;
  src.loop = true;
  src.loopStart = 0;
  src.loopEnd = 2;
  return { src, offset };
}

/** A filtered noise burst. */
function burst(
  ac: AudioContext,
  at: number,
  dur: number,
  opts: { type: BiquadFilterType; f0: number; f1?: number; q?: number; gain: number; attack?: number; pan?: [number, number] },
) {
  const { src, offset } = noiseSource(ac);
  const filter = ac.createBiquadFilter();
  filter.type = opts.type;
  filter.Q.value = opts.q ?? 0.8;
  filter.frequency.setValueAtTime(opts.f0, at);
  if (opts.f1) filter.frequency.exponentialRampToValueAtTime(opts.f1, at + dur);
  const g = ac.createGain();
  const attack = opts.attack ?? dur * 0.25;
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(opts.gain, at + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  const pan = ac.createStereoPanner();
  pan.pan.setValueAtTime(opts.pan?.[0] ?? 0, at);
  if (opts.pan) pan.pan.linearRampToValueAtTime(opts.pan[1], at + dur);
  src.connect(filter).connect(g).connect(pan).connect(master!);
  src.start(at, offset);
  src.stop(at + dur + 0.05);
}

function crinkle(ac: AudioContext, at: number, count: number, spread: number, gain: number) {
  for (let i = 0; i < count; i++) {
    const t = at + Math.random() * spread;
    burst(ac, t, 0.012 + Math.random() * 0.025, {
      type: "bandpass",
      f0: 2500 + Math.random() * 4000,
      q: 2.5,
      gain: gain * (0.4 + Math.random() * 0.6),
      attack: 0.002,
    });
  }
}

/** A full page turn lasting `dur` seconds. `dir` +1 sweeps right to left. */
export function pageTurn(dur = 1.1, dir: 1 | -1 = 1) {
  const ac = audio();
  if (!ac) return;
  const t = ac.currentTime + 0.01;
  crinkle(ac, t, 5, 0.12, 0.05);
  burst(ac, t + 0.05, dur * 0.62, {
    type: "bandpass",
    f0: 700,
    f1: 2600,
    q: 0.7,
    gain: 0.16,
    attack: dur * 0.3,
    pan: [0.35 * dir, -0.35 * dir],
  });
  burst(ac, t + dur * 0.62, 0.12, { type: "lowpass", f0: 900, q: 0.5, gain: 0.1, attack: 0.012, pan: [-0.3 * dir, -0.3 * dir] });
}

export function paperLift() {
  const ac = audio();
  if (!ac) return;
  crinkle(ac, ac.currentTime + 0.005, 4, 0.08, 0.045);
}

export function pageLand(dir: 1 | -1 = 1) {
  const ac = audio();
  if (!ac) return;
  const t = ac.currentTime + 0.005;
  burst(ac, t, 0.32, { type: "bandpass", f0: 1800, f1: 900, q: 0.6, gain: 0.07, attack: 0.08, pan: [0.2 * dir, -0.2 * dir] });
  burst(ac, t + 0.2, 0.1, { type: "lowpass", f0: 800, q: 0.5, gain: 0.08, attack: 0.01 });
}

export function coverOpen() {
  const ac = audio();
  if (!ac) return;
  const t = ac.currentTime + 0.01;
  // Card board leaving the block: low thock.
  const osc = ac.createOscillator();
  const g = ac.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(150, t);
  osc.frequency.exponentialRampToValueAtTime(70, t + 0.14);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.16, t + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
  osc.connect(g).connect(master!);
  osc.start(t);
  osc.stop(t + 0.2);
  burst(ac, t, 0.06, { type: "lowpass", f0: 1200, q: 0.5, gain: 0.08, attack: 0.004 });
  burst(ac, t + 0.12, 0.9, { type: "bandpass", f0: 500, f1: 1500, q: 0.6, gain: 0.1, attack: 0.35, pan: [0.3, -0.3] });
  burst(ac, t + 1.05, 0.16, { type: "lowpass", f0: 500, q: 0.6, gain: 0.14, attack: 0.012, pan: [-0.3, -0.3] });
}
