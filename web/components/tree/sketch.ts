/**
 * Small deterministic drawing helpers for the tree: seeded randomness,
 * hand-wobbled SVG paths and colour mixing. Pure functions, server-safe,
 * so the tree renders identically on the server and the client.
 */

export type Pt = readonly [number, number];

/** FNV-1a string hash, for turning ids into seeds. */
export function hashSeed(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** Mulberry32: tiny seeded PRNG returning [0, 1). */
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

function cubic(p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): [number, number] {
  const u = 1 - t;
  const a = u * u * u;
  const b = 3 * u * u * t;
  const c = 3 * u * t * t;
  const d = t * t * t;
  return [a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]];
}

export interface WobbleOptions {
  /** Max sideways drift, in layout units. */
  amplitude?: number;
  /** Number of sample points along the curve. */
  samples?: number;
  seed?: number;
}

/**
 * Sample a cubic Bézier, push each point sideways by a smooth seeded wobble
 * (zero at both ends, so lines still meet their targets), and return a
 * smooth path through the points (Catmull-Rom converted to Béziers).
 */
export function wobblyCubic(
  p0: Pt,
  p1: Pt,
  p2: Pt,
  p3: Pt,
  { amplitude = 2.2, samples = 14, seed = 1 }: WobbleOptions = {},
): string {
  const rand = rng(seed);
  const f1 = 1 + rand() * 1.5;
  const f2 = 2.5 + rand() * 2;
  const ph1 = rand() * Math.PI * 2;
  const ph2 = rand() * Math.PI * 2;

  const pts: [number, number][] = [];
  for (let i = 0; i <= samples; i++) {
    const t = i / samples;
    const [x, y] = cubic(p0, p1, p2, p3, t);
    const [x2, y2] = cubic(p0, p1, p2, p3, Math.min(1, t + 0.01));
    const [x1, y1] = cubic(p0, p1, p2, p3, Math.max(0, t - 0.01));
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len;
    const ny = dx / len;
    const env = Math.sin(Math.PI * t);
    const w = amplitude * env * (0.65 * Math.sin(t * f1 * Math.PI * 2 + ph1) + 0.35 * Math.sin(t * f2 * Math.PI * 2 + ph2));
    pts.push([x + nx * w, y + ny * w]);
  }
  return smoothPath(pts);
}

/** A slightly wobbly straight-ish line between two points. */
export function wobblyLine(a: Pt, b: Pt, opts: WobbleOptions = {}): string {
  const p1: Pt = [a[0] + (b[0] - a[0]) / 3, a[1] + (b[1] - a[1]) / 3];
  const p2: Pt = [a[0] + ((b[0] - a[0]) * 2) / 3, a[1] + ((b[1] - a[1]) * 2) / 3];
  return wobblyCubic(a, p1, p2, b, { samples: 8, ...opts });
}

function smoothPath(pts: readonly Pt[]): string {
  const f = (n: number) => n.toFixed(2);
  let d = `M${f(pts[0][0])} ${f(pts[0][1])}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${f(c1x)} ${f(c1y)} ${f(c2x)} ${f(c2y)} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d;
}

/* ---------------------------------------------------------------- colour */

function parseHex(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.replace(/./g, (c) => c + c) : h;
  const n = parseInt(full.slice(0, 6), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Mix two hex colours; t = 0 gives `a`, 1 gives `b`. */
export function mix(a: string, b: string, t: number): string {
  const ca = parseHex(a);
  const cb = parseHex(b);
  const out = ca.map((v, i) => Math.round(v + (cb[i] - v) * t));
  return `#${out.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/** Darken toward warm ink. */
export const shade = (hex: string, t: number) => mix(hex, "#2f2620", t);
/** Lighten toward cream paper. */
export const tint = (hex: string, t: number) => mix(hex, "#fbf8f1", t);
