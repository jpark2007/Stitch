/**
 * Outlines of the paper pieces that make up the puppet, in each bone's own
 * frame (rig units, the bone's pivot at the origin), plus the small toolkit
 * used to draw them and the signature items (itemArt.ts):
 *
 *   PathBuilder      lines, arcs and quadratic curves into a closed polyline
 *   hullOfCircles    a tapered capsule (convex hull of circles)
 *   resample         evenly spaced points along a closed outline
 *   deckle           push each point along its normal by seeded noise, so the
 *                    edge reads as hand-cut paper rather than vector art
 *   bonePieces()     the doll: torso, head, arms, hands, legs with feet
 *
 * Pure and dependency-free: plain number arrays, no three.js. kit.ts turns
 * these into geometry.
 */

/** A closed outline as flat [x0, y0, x1, y1, ...], counter-clockwise. */
export type Outline = number[];

export interface Piece {
  outline: Outline;
  /** Holes (clockwise or not; they are normalised when built). */
  holes?: Outline[];
}

/* ------------------------------------------------------------------------ */
/* Builder                                                                   */
/* ------------------------------------------------------------------------ */

/** Max distance between generated points along curves, rig units. */
const STEP = 0.0035;

export class PathBuilder {
  readonly pts: number[] = [];
  private x = 0;
  private y = 0;

  move(x: number, y: number): this {
    this.pts.push(x, y);
    this.x = x;
    this.y = y;
    return this;
  }

  line(x: number, y: number): this {
    return this.move(x, y);
  }

  /** Arc around (cx, cy) from angle a0 to a1 (radians; sign of a1 − a0 sets the direction). */
  arc(cx: number, cy: number, r: number, a0: number, a1: number, ry = r): this {
    const n = Math.max(2, Math.ceil((Math.abs(a1 - a0) * Math.max(r, ry)) / STEP));
    for (let i = 0; i <= n; i++) {
      const a = a0 + ((a1 - a0) * i) / n;
      this.move(cx + Math.cos(a) * r, cy + Math.sin(a) * ry);
    }
    return this;
  }

  /** Quadratic Bézier from the current point through control (qx, qy) to (x, y). */
  quad(qx: number, qy: number, x: number, y: number): this {
    const x0 = this.x;
    const y0 = this.y;
    const len = Math.hypot(qx - x0, qy - y0) + Math.hypot(x - qx, y - qy);
    const n = Math.max(2, Math.ceil(len / STEP));
    for (let i = 1; i <= n; i++) {
      const t = i / n;
      const u = 1 - t;
      this.move(u * u * x0 + 2 * u * t * qx + t * t * x, u * u * y0 + 2 * u * t * qy + t * t * y);
    }
    return this;
  }

  close(): Outline {
    return ensureCCW(dedupe(this.pts));
  }
}

function dedupe(p: number[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < p.length; i += 2) {
    const n = out.length;
    if (n >= 2 && Math.hypot(p[i] - out[n - 2], p[i + 1] - out[n - 1]) < 1e-7) continue;
    out.push(p[i], p[i + 1]);
  }
  if (out.length >= 4 && Math.hypot(out[0] - out[out.length - 2], out[1] - out[out.length - 1]) < 1e-7) {
    out.length -= 2;
  }
  return out;
}

/** Twice the signed area (positive when counter-clockwise). */
export function signedArea2(p: Outline): number {
  let a = 0;
  const n = p.length / 2;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    a += p[i * 2] * p[j * 2 + 1] - p[j * 2] * p[i * 2 + 1];
  }
  return a;
}

export function ensureCCW(p: Outline): Outline {
  if (signedArea2(p) >= 0) return p;
  const out: number[] = [];
  for (let i = p.length - 2; i >= 0; i -= 2) out.push(p[i], p[i + 1]);
  return out;
}

/** Map every point through a rotation by `angle` then a translation. */
export function transform(p: Outline, angle: number, tx: number, ty: number, sx = 1, sy = 1): Outline {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const out: number[] = new Array(p.length);
  for (let i = 0; i < p.length; i += 2) {
    const x = p[i] * sx;
    const y = p[i + 1] * sy;
    out[i] = x * c - y * s + tx;
    out[i + 1] = x * s + y * c + ty;
  }
  return ensureCCW(out);
}

/** A circle (or ellipse) outline. */
export function ellipse(cx: number, cy: number, rx: number, ry = rx): Outline {
  return new PathBuilder().arc(cx, cy, rx, 0, Math.PI * 2, ry).close();
}

/** A squircle-ish rounded box centred on (cx, cy), exponent n (2 = ellipse, higher = squarer). */
export function superellipse(cx: number, cy: number, hx: number, hy: number, n = 4, widthAt?: (u: number) => number): Outline {
  const pts: number[] = [];
  const steps = Math.max(48, Math.ceil((2 * Math.PI * Math.max(hx, hy)) / STEP));
  const e = 2 / n;
  for (let i = 0; i < steps; i++) {
    const t = (i / steps) * Math.PI * 2;
    const c = Math.cos(t);
    const s = Math.sin(t);
    const y = hy * Math.sign(s) * Math.pow(Math.abs(s), e);
    const u = (y + hy) / (2 * hy); // 0 at the bottom, 1 at the top
    const w = widthAt ? widthAt(u) : hx;
    pts.push(cx + w * Math.sign(c) * Math.pow(Math.abs(c), e), cy + y);
  }
  return ensureCCW(pts);
}

/** Convex hull (CCW) of a set of circles [x, y, r]: tapered capsules and paddles. */
export function hullOfCircles(circles: readonly (readonly [number, number, number])[]): Outline {
  const pts: [number, number][] = [];
  for (const [x, y, r] of circles) {
    const n = Math.max(24, Math.ceil((2 * Math.PI * r) / (STEP * 0.6)));
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      pts.push([x + Math.cos(a) * r, y + Math.sin(a) * r]);
    }
  }
  pts.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o: [number, number], a: [number, number], b: [number, number]) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: [number, number][] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: [number, number][] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  const hull = lower.slice(0, -1).concat(upper.slice(0, -1));
  return resample(hull.flat(), STEP);
}

/** A thick stroke along an open polyline, as a closed outline (round caps). */
export function stroke(path: readonly number[], width: number): Outline {
  const n = path.length / 2;
  const h = width / 2;
  const left: number[] = [];
  const right: number[] = [];
  for (let i = 0; i < n; i++) {
    const a = Math.max(0, i - 1);
    const b = Math.min(n - 1, i + 1);
    const dx = path[b * 2] - path[a * 2];
    const dy = path[b * 2 + 1] - path[a * 2 + 1];
    const l = Math.hypot(dx, dy) || 1;
    const nx = -dy / l;
    const ny = dx / l;
    left.push(path[i * 2] + nx * h, path[i * 2 + 1] + ny * h);
    right.push(path[i * 2] - nx * h, path[i * 2 + 1] - ny * h);
  }
  const b = new PathBuilder();
  for (let i = 0; i < n; i++) b.move(right[i * 2], right[i * 2 + 1]);
  const ex = path[(n - 1) * 2];
  const ey = path[(n - 1) * 2 + 1];
  const ea = Math.atan2(right[(n - 1) * 2 + 1] - ey, right[(n - 1) * 2] - ex);
  b.arc(ex, ey, h, ea, ea + Math.PI);
  for (let i = n - 1; i >= 0; i--) b.move(left[i * 2], left[i * 2 + 1]);
  const sx = path[0];
  const sy = path[1];
  const sa = Math.atan2(left[1] - sy, left[0] - sx);
  b.arc(sx, sy, h, sa, sa + Math.PI);
  return b.close();
}

/* ------------------------------------------------------------------------ */
/* Resampling and the hand-cut edge                                          */
/* ------------------------------------------------------------------------ */

export function perimeter(p: Outline): number {
  let L = 0;
  const n = p.length / 2;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    L += Math.hypot(p[j * 2] - p[i * 2], p[j * 2 + 1] - p[i * 2 + 1]);
  }
  return L;
}

/** Evenly spaced points (about `spacing` apart) along a closed outline. */
export function resample(p: Outline, spacing: number): Outline {
  const n = p.length / 2;
  const L = perimeter(p);
  const m = Math.max(8, Math.round(L / spacing));
  const step = L / m;
  const out: number[] = [];
  let seg = 0;
  let segStart = 0;
  let segLen = Math.hypot(p[2] - p[0], p[3] - p[1]);
  for (let k = 0; k < m; k++) {
    const d = k * step;
    while (d > segStart + segLen && seg < n - 1) {
      segStart += segLen;
      seg++;
      const j = (seg + 1) % n;
      segLen = Math.hypot(p[j * 2] - p[seg * 2], p[j * 2 + 1] - p[seg * 2 + 1]);
    }
    const j = (seg + 1) % n;
    const t = segLen > 0 ? (d - segStart) / segLen : 0;
    out.push(p[seg * 2] + (p[j * 2] - p[seg * 2]) * t, p[seg * 2 + 1] + (p[j * 2 + 1] - p[seg * 2 + 1]) * t);
  }
  return out;
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Periodic value noise over `n` samples with `cells` lattice cells, in [-1, 1]. */
function periodicNoise(n: number, cells: number, rand: () => number): Float64Array {
  const c = Math.max(1, cells);
  const lat = Array.from({ length: c }, () => rand() * 2 - 1);
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const x = (i / n) * c;
    const i0 = Math.floor(x);
    const f = x - i0;
    const s = f * f * (3 - 2 * f);
    out[i] = lat[i0 % c] + (lat[(i0 + 1) % c] - lat[i0 % c]) * s;
  }
  return out;
}

export interface DeckleOptions {
  /** Seed for this piece's edge. */
  seed: number;
  /** Slow wobble of a hand-guided scissor cut, rig units. Default 0.0022. */
  wobble?: number;
  /** Fine fibrous tooth, rig units. Default 0.0012. */
  tooth?: number;
  /** Point spacing along the edge, rig units. Default 0.0028. */
  spacing?: number;
}

/**
 * Resample a CCW outline and push each point along its outward normal by
 * seeded periodic noise: a slow wobble plus a fine tooth.
 */
export function deckle(p: Outline, o: DeckleOptions): Outline {
  const spacing = o.spacing ?? 0.0028;
  const pts = resample(p, spacing);
  const n = pts.length / 2;
  const L = perimeter(pts);
  const rand = mulberry32(o.seed * 2654435761);
  const slow = periodicNoise(n, Math.round(L / 0.035), rand);
  const fine = periodicNoise(n, Math.round(L / 0.0065), rand);
  const wob = o.wobble ?? 0.0022;
  const tooth = o.tooth ?? 0.0012;
  const out: number[] = new Array(pts.length);
  for (let i = 0; i < n; i++) {
    const a = (i - 1 + n) % n;
    const b = (i + 1) % n;
    const dx = pts[b * 2] - pts[a * 2];
    const dy = pts[b * 2 + 1] - pts[a * 2 + 1];
    const l = Math.hypot(dx, dy) || 1;
    // Outward normal of a CCW outline.
    const nx = dy / l;
    const ny = -dx / l;
    const off = slow[i] * wob + fine[i] * tooth;
    out[i * 2] = pts[i * 2] + nx * off;
    out[i * 2 + 1] = pts[i * 2 + 1] + ny * off;
  }
  return out;
}

/** Axis-aligned bounds [minX, minY, maxX, maxY]. */
export function bounds(p: Outline): [number, number, number, number] {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (let i = 0; i < p.length; i += 2) {
    x0 = Math.min(x0, p[i]);
    x1 = Math.max(x1, p[i]);
    y0 = Math.min(y0, p[i + 1]);
    y1 = Math.max(y1, p[i + 1]);
  }
  return [x0, y0, x1, y1];
}

/* ------------------------------------------------------------------------ */
/* The doll                                                                  */
/* ------------------------------------------------------------------------ */

/**
 * The head oval, in the head bone's frame. Shifted a touch forward (+x) so
 * the faceless head still reads as facing the way the puppet walks.
 */
export const HEAD = { cx: 0.007, cy: 0.122, rx: 0.074, ry: 0.09 } as const;

function torso(): Outline {
  // A soft tunic: narrow at the hip, a slight waist, square-ish shoulders.
  // Spans below the hip pivot so the far leg's top tucks behind it.
  const yMin = -0.055;
  const yMax = 0.302;
  const hip = 0.069;
  const shoulder = 0.1;
  return superellipse(0, (yMin + yMax) / 2, shoulder, (yMax - yMin) / 2, 3.4, (u) => {
    const base = hip + (shoulder - hip) * Math.pow(u, 1.35);
    return base - 0.007 * Math.sin(Math.PI * Math.min(1, u * 1.4));
  });
}

function head(): Outline {
  const { cx, cy, rx, ry } = HEAD;
  const nr = 0.017; // neck half-width
  // The neck ends in a small round tab just under the pin (the head's pivot),
  // so little of the lighter head paper shows on the torso.
  const tab = 0.0125;
  const tR = -Math.acos((nr - cx) / rx);
  const tL = -Math.acos((-nr - cx) / rx) + Math.PI * 2;
  return new PathBuilder()
    .arc(0, 0, tab, Math.PI, Math.PI * 2)
    .line(nr, 0.006)
    .line(nr, cy + Math.sin(tR) * ry)
    .arc(cx, cy, rx, tR, tL, ry)
    .line(-nr, 0.006)
    .close();
}

/** Foot geometry shared by the lower leg and the shoe. */
function footOf(len: number) {
  const sole = -len;
  return {
    sole,
    heel: { x: -0.007, y: sole + 0.018, r: 0.018 },
    toe: { x: 0.06, y: sole + 0.0125, r: 0.0125 },
  };
}

/** A little paper shoe over the foot of the lower leg. */
function shoe(len: number): Outline {
  const { sole, heel, toe } = footOf(len);
  const g = 0.0016; // overhang, so the shoe covers the foot's cut edge
  return new PathBuilder()
    .move(heel.x, sole - g)
    .line(toe.x, sole - g)
    .arc(toe.x, toe.y, toe.r + g, -Math.PI / 2, Math.PI / 2)
    .quad(0.034, toe.y + toe.r + 0.005, 0.026, sole + 0.04)
    .quad(0.0, sole + 0.05, -0.028, sole + 0.037)
    .line(heel.x - heel.r - g, heel.y)
    .arc(heel.x, heel.y, heel.r + g, Math.PI, Math.PI * 1.5)
    .close();
}

function lowerLeg(len: number): Outline {
  // Shin with a foot pointing forward (+x): the clearest facing cue a
  // faceless profile doll has. The sole sits exactly at the bone's tip.
  const { sole, heel, toe } = footOf(len);
  const knee = 0.028;
  return new PathBuilder()
    .move(heel.x, sole)
    .line(toe.x, sole)
    .arc(toe.x, toe.y, toe.r, -Math.PI / 2, Math.PI / 2)
    .quad(0.03, toe.y + toe.r + 0.002, 0.022, sole + 0.05)
    .line(knee, 0)
    .arc(0, 0, knee, 0, Math.PI)
    .line(heel.x - heel.r, heel.y)
    .arc(heel.x, heel.y, heel.r, Math.PI, Math.PI * 1.5)
    .close();
}

/** The shoe outline, in the lower leg's frame. */
export function shoePiece(lowerLegLength: number): Piece {
  return { outline: shoe(lowerLegLength) };
}

/** Per-bone piece outlines (before the hand-cut edge is applied). */
export function bonePieces(lengths: Record<string, number>): Record<string, Piece> {
  const len = (b: string, d: number) => lengths[b] ?? d;
  const ua = len("arm_r.upper", 0.17);
  const la = len("arm_r.lower", 0.17);
  const ul = len("leg_r.upper", 0.24);
  const ll = len("leg_r.lower", 0.24);
  const upperArm = hullOfCircles([
    [0, 0, 0.027],
    [0, -ua, 0.022],
  ]);
  const lowerArm = hullOfCircles([
    [0, 0, 0.022],
    [0, -la, 0.018],
  ]);
  const hand = hullOfCircles([
    [0, 0.002, 0.019],
    [0.007, -0.03, 0.024],
  ]);
  const upperLeg = hullOfCircles([
    [0, 0, 0.034],
    [0, -ul, 0.028],
  ]);
  const shin = lowerLeg(ll);
  return {
    torso: { outline: torso() },
    head: { outline: head() },
    "arm_l.upper": { outline: upperArm },
    "arm_r.upper": { outline: upperArm },
    "arm_l.lower": { outline: lowerArm },
    "arm_r.lower": { outline: lowerArm },
    hand_l: { outline: hand },
    hand_r: { outline: hand },
    "leg_l.upper": { outline: upperLeg },
    "leg_r.upper": { outline: upperLeg },
    "leg_l.lower": { outline: shin },
    "leg_r.lower": { outline: shin },
  };
}
