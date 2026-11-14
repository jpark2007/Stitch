/**
 * Generic easing, interpolation and spring helpers for the stage.
 *
 * Deliberately format-free: the compiled timeline format is a shared contract
 * (ARCHITECTURE.md section 3), so nothing here knows about tracks or keys. These
 * are the maths the stage's players are built from.
 *
 * Everything is pure and allocation-free on the hot path, so it is safe to call
 * from useFrame.
 */

export type Vec3 = [number, number, number];

/* ------------------------------------------------------------------------ */
/* Scalars                                                                   */
/* ------------------------------------------------------------------------ */

export const clamp = (x: number, lo = 0, hi = 1) => (x < lo ? lo : x > hi ? hi : x);
export const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
/** Where x sits between a and b, clamped to 0..1. */
export const invLerp = (a: number, b: number, x: number) => (a === b ? (x < a ? 0 : 1) : clamp((x - a) / (b - a)));
export const remap = (x: number, a0: number, a1: number, b0: number, b1: number) => lerp(b0, b1, invLerp(a0, a1, x));

/* ------------------------------------------------------------------------ */
/* Easing (0..1 -> 0..1)                                                     */
/* ------------------------------------------------------------------------ */

export type Ease = (k: number) => number;

export const linear: Ease = (k) => k;
export const smoothstep: Ease = (k) => {
  const x = clamp(k);
  return x * x * (3 - 2 * x);
};
export const smootherstep: Ease = (k) => {
  const x = clamp(k);
  return x * x * x * (x * (x * 6 - 15) + 10);
};
export const easeInOutCubic: Ease = (k) => {
  const x = clamp(k);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
};
export const easeOutCubic: Ease = (k) => 1 - Math.pow(1 - clamp(k), 3);
export const easeInCubic: Ease = (k) => Math.pow(clamp(k), 3);
/** Slow, cinematic in-out (quintic-ish with a long tail). Good for camera moves. */
export const easeInOutSine: Ease = (k) => -(Math.cos(Math.PI * clamp(k)) - 1) / 2;

/* ------------------------------------------------------------------------ */
/* Vectors (written into `out` so nothing allocates per frame)               */
/* ------------------------------------------------------------------------ */

export function lerp3(out: Vec3, a: Readonly<Vec3>, b: Readonly<Vec3>, k: number): Vec3 {
  out[0] = a[0] + (b[0] - a[0]) * k;
  out[1] = a[1] + (b[1] - a[1]) * k;
  out[2] = a[2] + (b[2] - a[2]) * k;
  return out;
}

/* ------------------------------------------------------------------------ */
/* Critically damped springs                                                 */
/* ------------------------------------------------------------------------ */

/**
 * Closed-form critically damped spring, from (x0, v0) towards `target`, after
 * `t` seconds with angular frequency `omega` (higher is snappier; settle time
 * is roughly 4.6 / omega). Writes [x, v] into `out`.
 *
 * Starting at rest it never overshoots. Starting with velocity towards the
 * target faster than `omega * distance` it overshoots exactly once and settles,
 * which is how a card pop-up snaps open.
 */
export function springStep(out: [number, number], x0: number, v0: number, target: number, omega: number, t: number) {
  const e = x0 - target;
  const c = v0 + omega * e;
  const decay = Math.exp(-omega * t);
  out[0] = target + (e + c * t) * decay;
  out[1] = (v0 - omega * c * t) * decay;
  return out;
}

/** A scheduled change of a spring's target: at time `at`, aim for `target`,
 *  adding `kick` (units per second, signed towards the new target) of velocity. */
export interface SpringEvent {
  at: number;
  target: number;
  kick?: number;
}

const tmp: [number, number] = [0, 0];

/**
 * Evaluate a spring driven by a list of target changes, at time `t`, from
 * scratch. Deterministic and frame-rate independent, so the same time always
 * gives the same pose (headless frame capture and seeking rely on this).
 * `events` must be sorted by `at`. Before the first event the value is `rest`.
 */
export function springAt(events: readonly SpringEvent[], t: number, omega: number, rest = 0): number {
  let x = rest;
  let v = 0;
  let from = 0;
  let target = rest;
  let started = false;
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (ev.at > t) break;
    if (started) {
      springStep(tmp, x, v, target, omega, ev.at - from);
      x = tmp[0];
      v = tmp[1];
    }
    started = true;
    from = ev.at;
    target = ev.target;
    if (ev.kick) v += Math.sign(target - x || 1) * ev.kick;
  }
  if (!started) return rest;
  springStep(tmp, x, v, target, omega, t - from);
  return tmp[0];
}

/**
 * Per-frame critically damped smoothing of a value towards a moving target
 * (the "SmoothDamp" from game engines). `state` holds [value, velocity] and is
 * updated in place. `smoothTime` is roughly the time to reach the target.
 */
export function smoothDamp(state: [number, number], target: number, smoothTime: number, dt: number): number {
  const omega = 2 / Math.max(1e-4, smoothTime);
  springStep(state, state[0], state[1], target, omega, Math.min(dt, 0.1));
  return state[0];
}

/** Frame-rate independent exponential approach (`lambda` per second). */
export const damp = (current: number, target: number, lambda: number, dt: number) =>
  lerp(current, target, 1 - Math.exp(-lambda * dt));

/* ------------------------------------------------------------------------ */
/* Deterministic noise                                                       */
/* ------------------------------------------------------------------------ */

/** Hash an integer (and optional salt) to [0, 1). Stable across runs. */
export function hash01(n: number, salt = 0): number {
  let h = Math.imul((n | 0) ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul(salt + 0x27d4eb2f, 0xc2b2ae35);
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d);
  h ^= h >>> 12;
  h = Math.imul(h, 0x297a2d39);
  h ^= h >>> 15;
  return (h >>> 0) / 4294967296;
}

/** Seeded PRNG (mulberry32). */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
