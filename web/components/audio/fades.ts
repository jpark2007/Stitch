/**
 * Gain curves and the transport's state machine: the parts of the music
 * engine that are decisions rather than sound, kept pure for the unit tests
 * (fades.test.ts).
 *
 *   Ramp / rampValue / rampPoints   a fade as data: its value at any time and
 *                                   the breakpoints that automate an AudioParam
 *   stepTransport / tickTransport   when to resume, fade in, fade out and
 *                                   suspend the AudioContext as mute, the open
 *                                   book and the era change
 *
 * Free of runtime imports: Node's test runner imports it directly.
 */

export const dbToGain = (db: number) => Math.pow(10, db / 20);
export const gainToDb = (g: number) => (g <= 0 ? -Infinity : 20 * Math.log10(g));

/** Crossfade between two eras (each voice ramps over this long). */
export const CROSSFADE = 2.6;
/** Narration ducking: down fast, back up slowly. */
export const DUCK_DB = -10;
export const DUCK_DOWN = 0.45;
export const DUCK_UP = 1.2;
/** Mute is quick (it is a button); the book closing is a slower ebb. */
export const MUTE_FADE = 0.3;
export const UNMUTE_FADE = 0.8;
export const ACTIVE_FADE = 1.6;

/**
 * "in" rises along sin (equal-power with "out"), "out" falls along cos, and
 * "smooth" is a raised cosine for level changes that are not crossfades.
 */
export type Shape = "in" | "out" | "smooth";

export interface Ramp {
  from: number;
  to: number;
  /** Start time, seconds (AudioContext time). */
  t0: number;
  dur: number;
  shape: Shape;
}

/** The unit curve 0..1 for x in 0..1. */
export function shapeAt(shape: Shape, x: number): number {
  const u = Math.max(0, Math.min(1, x));
  if (shape === "in") return Math.sin((u * Math.PI) / 2);
  if (shape === "out") return 1 - Math.cos((u * Math.PI) / 2);
  return 0.5 - 0.5 * Math.cos(u * Math.PI);
}

/** A ramp's value at time `t` (holds `from` before it, `to` after it). */
export function rampValue(r: Ramp, t: number): number {
  if (r.dur <= 0 || t >= r.t0 + r.dur) return r.to;
  if (t <= r.t0) return r.from;
  return r.from + (r.to - r.from) * shapeAt(r.shape, (t - r.t0) / r.dur);
}

/**
 * Breakpoints [time, value] approximating the ramp with straight segments
 * (linearRampToValueAtTime never throws on overlapping automation, unlike
 * setValueCurveAtTime). The first point is the ramp's start, the last its end.
 */
export function rampPoints(r: Ramp, segments = 24): [number, number][] {
  if (r.dur <= 0) return [[r.t0, r.to]];
  const pts: [number, number][] = [];
  for (let i = 0; i <= segments; i++) {
    const t = r.t0 + (r.dur * i) / segments;
    pts.push([t, rampValue(r, t)]);
  }
  return pts;
}

/**
 * Pick the shape for a fade from `from` to `to`: equal-power for crossfades,
 * raised cosine otherwise.
 */
export function crossfadeShape(from: number, to: number): Shape {
  return to >= from ? "in" : "out";
}

/** The music's absolute output ceiling: -12 dBFS. */
export const CEILING = 0.25;

/**
 * A WaveShaper curve over input -1..1: c * tanh(x / c). Within a few dB of
 * silence it is the identity; it bends smoothly toward ±c and never passes
 * it, so the output can never exceed the ceiling or clip.
 */
export function softClipCurve(n: number, c: number): Float32Array {
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    out[i] = c * Math.tanh(x / c);
  }
  return out;
}

/** Largest change in value between consecutive points (a click check). */
export function maxStep(points: [number, number][]): number {
  let m = 0;
  for (let i = 1; i < points.length; i++) m = Math.max(m, Math.abs(points[i][1] - points[i - 1][1]));
  return m;
}

/* ------------------------------------------------------------------------ */
/* Transport                                                                 */
/* ------------------------------------------------------------------------ */

/** What the engine has been asked for. */
export interface TransportInput {
  muted: boolean;
  /** The book is open (the lab's "active"). */
  active: boolean;
  /** An era is set or a voice is still fading out. */
  sounding: boolean;
}

/**
 *   asleep    context suspended (or not created); nothing scheduled
 *   playing   level fading to / held at 1
 *   stopping  level fading to 0; suspend at `until`
 */
export type Phase = { kind: "asleep" } | { kind: "playing" } | { kind: "stopping"; until: number };

export type TransportAction =
  | { kind: "resume" }
  | { kind: "level"; to: number; dur: number }
  | { kind: "suspend" };

export function wantsSound(i: TransportInput) {
  return !i.muted && i.active && i.sounding;
}

/**
 * React to a change of input at time `now`. `why` picks the fade length:
 * the mute button is quick, the book opening or closing is slow.
 */
export function stepTransport(
  phase: Phase,
  input: TransportInput,
  now: number,
  why: "mute" | "active" | "era" = "era",
): { phase: Phase; actions: TransportAction[] } {
  const want = wantsSound(input);
  if (want) {
    if (phase.kind === "playing") return { phase, actions: [] };
    const dur = why === "mute" ? UNMUTE_FADE : ACTIVE_FADE;
    const actions: TransportAction[] = phase.kind === "asleep" ? [{ kind: "resume" }] : [];
    actions.push({ kind: "level", to: 1, dur });
    return { phase: { kind: "playing" }, actions };
  }
  if (phase.kind === "asleep") return { phase, actions: [] };
  if (!input.sounding && input.active && !input.muted) {
    // Nothing left to play: the voices have faded themselves; sleep now.
    return { phase: { kind: "asleep" }, actions: [{ kind: "level", to: 0, dur: 0.05 }, { kind: "suspend" }] };
  }
  const dur = why === "mute" || input.muted ? MUTE_FADE : ACTIVE_FADE;
  if (phase.kind === "stopping") {
    // Already fading out; a mute during a slow close finishes sooner.
    const until = Math.min(phase.until, now + dur);
    if (until === phase.until) return { phase, actions: [] };
    return { phase: { kind: "stopping", until }, actions: [{ kind: "level", to: 0, dur }] };
  }
  return { phase: { kind: "stopping", until: now + dur }, actions: [{ kind: "level", to: 0, dur }] };
}

/** Advance time: a finished fade-out suspends the context. */
export function tickTransport(phase: Phase, now: number): { phase: Phase; actions: TransportAction[] } {
  if (phase.kind === "stopping" && now >= phase.until) return { phase: { kind: "asleep" }, actions: [{ kind: "suspend" }] };
  return { phase, actions: [] };
}
