/**
 * Placeholder art for props with no art yet (`art: null`, CONTRACTS.md
 * decision 8): a cut-paper card in the prop's colour with a simple silhouette
 * for its size class, so it still reads as an object.
 *
 *   tiny       a button or coin
 *   hand       a little stack (books, boxes)
 *   carried    a case with a handle on top
 *   furniture  a small table
 *   vehicle    a car (wheels drawn in unless the prop has wheel parts)
 *   building   a house
 *
 * Drawn once on a <canvas> as glued layers of flat paper (no gradients, no
 * outlines), with a hand-cut wobble seeded by the prop id. The colour map is
 * opaque and the silhouette lives in a separate alpha map, so edges never pick
 * up dark fringes. Textures are cached by spec and reference counted.
 *
 * Browser-only.
 */

import { CanvasTexture, ClampToEdgeWrapping, Color, NoColorSpace, SRGBColorSpace, type Texture } from "three";
import type { PropSize } from "./rig";

export interface PlaceholderSpec {
  /** A whole prop's card, or one of its parts. */
  kind: "body" | "wheel" | "piece";
  size: PropSize;
  color: string;
  /** Width / height of the card. */
  aspect: number;
  /** Any stable string; varies the hand-cut wobble and details. */
  seed: string;
  /** Vehicles: draw the wheels into the body (false when wheel parts exist). */
  wheels?: boolean;
}

export interface PlaceholderArt {
  map: Texture;
  alphaMap: Texture;
}

const DEFAULT_COLOR = "#c4a37a"; // kraft
const INK = new Color("#2f2620");
const CREAM = new Color("#fbf3e4");

/* ------------------------------------------------------------------------ */
/* Cache                                                                     */
/* ------------------------------------------------------------------------ */

const cache = new Map<string, { art: PlaceholderArt; refs: number }>();

export function placeholderKey(s: PlaceholderSpec): string {
  return [s.kind, s.size, s.color, s.aspect.toFixed(3), s.seed, s.wheels ? 1 : 0].join("|");
}

/** Get (and hold a reference to) the placeholder textures for a spec. */
export function acquirePlaceholder(spec: PlaceholderSpec): PlaceholderArt {
  const key = placeholderKey(spec);
  const hit = cache.get(key);
  if (hit) {
    hit.refs++;
    return hit.art;
  }
  const art = paint(spec);
  cache.set(key, { art, refs: 1 });
  return art;
}

export function releasePlaceholder(spec: PlaceholderSpec): void {
  const key = placeholderKey(spec);
  const slot = cache.get(key);
  if (!slot || --slot.refs > 0) return;
  cache.delete(key);
  slot.art.map.dispose();
  slot.art.alphaMap.dispose();
}

/* ------------------------------------------------------------------------ */
/* Colour                                                                    */
/* ------------------------------------------------------------------------ */

function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function safeColor(c: string): Color {
  const out = new Color(DEFAULT_COLOR);
  try {
    out.setStyle(c);
  } catch {
    /* keep the default */
  }
  return out;
}

interface Swatch {
  base: string;
  light: string;
  lighter: string;
  dark: string;
  deep: string;
  /** Torn-paper core: the pale fibres at a cut edge. */
  core: string;
  /** Pale glass / paper window, warm. */
  pane: string;
}

function swatch(color: string): Swatch {
  const base = safeColor(color);
  const mix = (to: Color, k: number) => "#" + base.clone().lerp(to, k).getHexString();
  // Lighter in lightness only, so dark, saturated colours (navy, bottle
  // green) stay themselves instead of greying toward cream.
  const hsl = { h: 0, s: 0, l: 0 };
  base.getHSL(hsl);
  const lift = "#" + new Color().setHSL(hsl.h, hsl.s, Math.min(0.85, hsl.l + 0.09)).getHexString();
  return {
    base: "#" + base.getHexString(),
    light: lift,
    lighter: mix(CREAM, 0.5),
    dark: mix(INK, 0.22),
    deep: mix(INK, 0.45),
    core: mix(CREAM, 0.7),
    pane: "#" + new Color("#f1e9d6").lerp(base, 0.12).getHexString(),
  };
}

/* ------------------------------------------------------------------------ */
/* Hand-cut shapes                                                           */
/* ------------------------------------------------------------------------ */

type Pts = number[]; // flat x, y pairs, closed

function rrect(x: number, y: number, w: number, h: number, r: number, tl = r, tr = r, br = r, bl = r): Pts {
  const pts: Pts = [];
  const corner = (cx: number, cy: number, rad: number, a0: number) => {
    const n = rad > 0 ? 8 : 1;
    for (let i = 0; i <= n; i++) {
      const a = a0 + (i / n) * (Math.PI / 2);
      pts.push(cx + Math.cos(a) * rad, cy + Math.sin(a) * rad);
    }
  };
  const k = Math.min(w, h) / 2;
  tl = Math.min(tl, k);
  tr = Math.min(tr, k);
  br = Math.min(br, k);
  bl = Math.min(bl, k);
  corner(x + tl, y + tl, tl, Math.PI);
  corner(x + w - tr, y + tr, tr, -Math.PI / 2);
  corner(x + w - br, y + h - br, br, 0);
  corner(x + bl, y + h - bl, bl, Math.PI / 2);
  return pts;
}

function ellipse(cx: number, cy: number, rx: number, ry: number, n = 48): Pts {
  const pts: Pts = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    pts.push(cx + Math.cos(a) * rx, cy + Math.sin(a) * ry);
  }
  return pts;
}

/** Subdivide a polygon so the wobble has points to move along long edges. */
function densify(pts: Pts, step: number): Pts {
  const out: Pts = [];
  const n = pts.length / 2;
  for (let i = 0; i < n; i++) {
    const x0 = pts[i * 2];
    const y0 = pts[i * 2 + 1];
    const x1 = pts[((i + 1) % n) * 2];
    const y1 = pts[((i + 1) % n) * 2 + 1];
    const segs = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / step));
    for (let s = 0; s < segs; s++) out.push(x0 + ((x1 - x0) * s) / segs, y0 + ((y1 - y0) * s) / segs);
  }
  return out;
}

/** Nudge every point by smooth noise, like scissors that never quite follow
 *  the line. */
function wobble(pts: Pts, amp: number, rand: () => number, step = 6): Pts {
  const d = densify(pts, step);
  const n = d.length / 2;
  let nx = new Float32Array(n).map(() => rand() - 0.5);
  let ny = new Float32Array(n).map(() => rand() - 0.5);
  for (let pass = 0; pass < 3; pass++) {
    const sx = new Float32Array(n);
    const sy = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const a = (i + n - 1) % n;
      const b = (i + 1) % n;
      sx[i] = (nx[a] + 2 * nx[i] + nx[b]) / 4;
      sy[i] = (ny[a] + 2 * ny[i] + ny[b]) / 4;
    }
    nx = sx;
    ny = sy;
  }
  const out: Pts = new Array(d.length);
  for (let i = 0; i < n; i++) {
    out[i * 2] = d[i * 2] + nx[i] * amp * 2;
    out[i * 2 + 1] = d[i * 2 + 1] + ny[i] * amp * 2;
  }
  return out;
}

function path(pts: Pts): Path2D {
  const p = new Path2D();
  p.moveTo(pts[0], pts[1]);
  for (let i = 2; i < pts.length; i += 2) p.lineTo(pts[i], pts[i + 1]);
  p.closePath();
  return p;
}

/* ------------------------------------------------------------------------ */
/* Painter                                                                   */
/* ------------------------------------------------------------------------ */

interface Kit {
  W: number;
  H: number;
  /** Short side in px. */
  S: number;
  sw: Swatch;
  rand: () => number;
  /** Cut a shape with the hand wobble. */
  cut: (pts: Pts, amp?: number) => Path2D;
  /** Add a piece to the silhouette (alpha) and paint it. With `lift`, it casts
   *  a soft shadow on the pieces already there (a stacked book). */
  base: (p: Path2D, fill: string, lift?: number) => void;
  /** Paint a layer glued on top: soft shadow under it, pale cut rim. */
  layer: (p: Path2D, fill: string, lift?: number) => void;
}

function makeKit(color: CanvasRenderingContext2D, alpha: CanvasRenderingContext2D, W: number, H: number, sw: Swatch, rand: () => number): Kit {
  const S = Math.min(W, H);
  const amp = Math.max(0.6, S * 0.0035);
  const silhouette = new Path2D();
  const rim = (p: Path2D, width: number) => {
    color.save();
    color.clip(p);
    color.strokeStyle = sw.core;
    color.globalAlpha = 0.35;
    color.lineWidth = width;
    color.stroke(p);
    color.restore();
  };
  // A layer glued on top: its soft shadow stays inside the card's silhouette,
  // so it never spills past the cut edge.
  const glued = (p: Path2D, fill: string, lift: number) => {
    color.save();
    color.clip(silhouette);
    color.shadowColor = "rgba(47, 30, 18, 0.32)";
    color.shadowBlur = S * 0.012 * lift;
    color.shadowOffsetX = S * 0.002 * lift;
    color.shadowOffsetY = S * 0.006 * lift;
    color.fillStyle = fill;
    color.fill(p);
    color.restore();
  };
  return {
    W,
    H,
    S,
    sw,
    rand,
    cut: (pts, a = amp) => path(wobble(pts, a, rand, Math.max(3, S / 80))),
    base: (p, fill, lift = 0) => {
      silhouette.addPath(p);
      alpha.fillStyle = "#fff";
      alpha.fill(p);
      if (lift > 0) glued(p, fill, lift);
      else {
        color.fillStyle = fill;
        color.fill(p);
      }
      rim(p, Math.max(1.5, S * 0.008));
    },
    layer: (p, fill, lift = 1) => {
      glued(p, fill, lift);
      rim(p, Math.max(1.2, S * 0.006));
    },
  };
}

type Painter = (k: Kit, spec: PlaceholderSpec) => void;

/** A button: a disc with a raised rim and a small highlight. */
const paintTiny: Painter = ({ W, H, cut, base, layer, sw }) => {
  const cx = W / 2;
  const cy = H / 2;
  const rx = W * 0.47;
  const ry = H * 0.47;
  base(cut(ellipse(cx, cy, rx, ry)), sw.dark);
  layer(cut(ellipse(cx, cy - ry * 0.04, rx * 0.84, ry * 0.84)), sw.base);
  layer(cut(ellipse(cx - rx * 0.22, cy - ry * 0.26, rx * 0.26, ry * 0.18)), sw.lighter, 0.4);
};

/** A small stack lying flat, spines out: books, boxes, a parcel. */
const paintHand: Painter = ({ W, H, cut, base, layer, sw, rand }) => {
  const count = W / H >= 1.4 ? 2 : 3;
  const slabH = H / count;
  const tones = [sw.base, sw.dark, sw.light];
  for (let i = 0; i < count; i++) {
    // Bottom slab is the widest; each one above sits a little askew and
    // overlaps the one below, casting a soft shadow on it.
    const y = H - (i + 1) * slabH + (i > 0 ? slabH * 0.04 : 0);
    const h = slabH * (i > 0 ? 1.0 : 0.98);
    const w = W * (0.97 - i * 0.06 - rand() * 0.03);
    const x = (W - w) / 2 + (rand() - 0.5) * W * 0.07;
    const r = h * 0.1;
    base(cut(rrect(x, y, w, h, r)), tones[(i + (count === 2 ? 1 : 0)) % tones.length], i > 0 ? 0.6 : 0);
    // Spine bands near each end, and a small title label between them.
    for (const u of [0.1, 0.86]) layer(cut(rrect(x + w * u, y + h * 0.1, w * 0.035, h * 0.8, 1)), sw.lighter, 0.25);
    layer(cut(rrect(x + w * 0.4, y + h * 0.34, w * 0.2, h * 0.32, h * 0.05)), sw.lighter, 0.35);
  }
};

/** A case: body, flap and a handle loop on top (where it is usually held). */
const paintCarried: Painter = ({ W, H, S, cut, base, layer, sw }) => {
  const top = H * 0.26;
  const handleW = W * 0.34;
  const bar = S * 0.075;
  const hx = (W - handleW) / 2;
  // Handle: a loop made of a rounded bar with a hole cut by the body overlap.
  base(cut(rrect(hx, H * 0.03, handleW, bar, bar / 2)), sw.deep);
  base(cut(rrect(hx, H * 0.03, bar, top - H * 0.02, bar / 2)), sw.deep);
  base(cut(rrect(hx + handleW - bar, H * 0.03, bar, top - H * 0.02, bar / 2)), sw.deep);
  const body = cut(rrect(W * 0.04, top, W * 0.92, H - top, S * 0.1));
  base(body, sw.base);
  // Flap and clasp.
  layer(cut(rrect(W * 0.04, top, W * 0.92, (H - top) * 0.3, S * 0.1, S * 0.1, S * 0.1, S * 0.03, S * 0.03)), sw.dark);
  layer(cut(rrect(W * 0.46, top + (H - top) * 0.22, W * 0.08, (H - top) * 0.16, S * 0.02)), sw.lighter, 0.6);
  // Corner patches.
  layer(cut(rrect(W * 0.04, H * 0.86, W * 0.12, H * 0.14, S * 0.04, 0, S * 0.02, S * 0.1, S * 0.1)), sw.deep, 0.4);
  layer(cut(rrect(W * 0.84, H * 0.86, W * 0.12, H * 0.14, S * 0.04, S * 0.02, 0, S * 0.1, S * 0.1)), sw.deep, 0.4);
};

/** A small table: top, apron, two legs and a stretcher. */
const paintFurniture: Painter = ({ W, H, S, cut, base, layer, sw }) => {
  const topY = H * 0.14;
  const topH = H * 0.13;
  const legW = Math.max(W * 0.075, S * 0.07);
  base(cut(rrect(W * 0.1, topY + topH * 0.6, legW, H - topY - topH * 0.6, legW * 0.25)), sw.dark);
  base(cut(rrect(W * 0.9 - legW, topY + topH * 0.6, legW, H - topY - topH * 0.6, legW * 0.25)), sw.dark);
  base(cut(rrect(W * 0.12, H * 0.7, W * 0.76, H * 0.045, 2)), sw.deep);
  base(cut(rrect(W * 0.01, topY, W * 0.98, topH, topH * 0.35)), sw.base);
  layer(cut(rrect(W * 0.08, topY + topH, W * 0.84, H * 0.08, 2)), sw.dark, 0.8);
  layer(cut(rrect(W * 0.03, topY + topH * 0.12, W * 0.94, topH * 0.3, topH * 0.15)), sw.light, 0.3);
};

/** Wheel centres for a vehicle card, as image fractions (also the defaults
 *  for wheel parts the art does not place). */
export const VEHICLE_WHEELS = [
  [0.22, 0.82],
  [0.79, 0.82],
] as const;
/** Wheel radius as a fraction of the card height. */
export const VEHICLE_WHEEL_RADIUS = 0.17;

/** A rounded car, facing right. */
const paintVehicle: Painter = (k, spec) => {
  const { W, H, cut, base, layer, sw } = k;
  const bodyTop = H * 0.4;
  const bodyBot = H * 0.84;
  const cabin: Pts = [
    W * 0.22, bodyTop + H * 0.02,
    W * 0.31, H * 0.1,
    W * 0.66, H * 0.1,
    W * 0.79, bodyTop + H * 0.02,
  ];
  base(cut(densify(cabin, 4)), sw.base);
  base(cut(rrect(W * 0.02, bodyTop, W * 0.96, bodyBot - bodyTop, H * 0.14, H * 0.14, H * 0.2, H * 0.08, H * 0.08)), sw.base);
  // Windows.
  layer(cut(densify([W * 0.27, bodyTop - H * 0.0, W * 0.34, H * 0.16, W * 0.48, H * 0.16, W * 0.48, bodyTop], 4)), sw.pane, 0.6);
  layer(cut(densify([W * 0.52, bodyTop, W * 0.52, H * 0.16, W * 0.64, H * 0.16, W * 0.74, bodyTop], 4)), sw.pane, 0.6);
  // Waist stripe, bumpers, lamps.
  layer(cut(rrect(W * 0.04, H * 0.55, W * 0.92, H * 0.05, 2)), sw.light, 0.4);
  layer(cut(rrect(W * 0.9, H * 0.47, W * 0.06, H * 0.07, H * 0.03)), sw.core, 0.4);
  layer(cut(rrect(W * 0.02, H * 0.48, W * 0.04, H * 0.06, H * 0.02)), sw.deep, 0.4);
  layer(cut(rrect(W * 0.01, H * 0.73, W * 0.1, H * 0.06, H * 0.03)), sw.deep, 0.5);
  layer(cut(rrect(W * 0.89, H * 0.73, W * 0.1, H * 0.06, H * 0.03)), sw.deep, 0.5);
  // Wheel arches.
  const r = H * VEHICLE_WHEEL_RADIUS;
  for (const [u, v] of VEHICLE_WHEELS) layer(cut(ellipse(W * u, H * v, r * 1.18, r * 1.18)), sw.deep, 0.3);
  if (spec.wheels) {
    for (const [u, v] of VEHICLE_WHEELS) {
      const cx = W * u;
      const cy = H * v;
      base(cut(ellipse(cx, cy, r, r)), "#3b322b");
      layer(cut(ellipse(cx, cy, r * 0.45, r * 0.45)), sw.lighter, 0.4);
    }
  }
};

/** A house: walls, roof, chimney, door and two windows. */
const paintBuilding: Painter = ({ W, H, S, cut, base, layer, sw }) => {
  const eave = H * 0.42;
  base(cut(rrect(W * 0.72, H * 0.1, W * 0.08, H * 0.2, 2)), sw.deep);
  base(cut(rrect(W * 0.1, eave - H * 0.02, W * 0.8, H - eave + H * 0.02, 3)), sw.light);
  base(cut(densify([W * 0.02, eave, W * 0.5, H * 0.04, W * 0.98, eave], 6)), sw.dark);
  layer(cut(densify([W * 0.12, eave - H * 0.02, W * 0.5, H * 0.1, W * 0.88, eave - H * 0.02], 6)), sw.base, 0.5);
  // Door.
  const dw = W * 0.16;
  layer(cut(rrect(W * 0.5 - dw / 2, H * 0.68, dw, H * 0.32, dw * 0.5, dw * 0.5, dw * 0.5, 0, 0)), sw.deep, 0.6);
  // Windows with a pale pane and a mullion.
  for (const x of [0.2, 0.64]) {
    const ww = W * 0.16;
    const wy = H * 0.54;
    const wh = H * 0.17;
    layer(cut(rrect(W * x, wy, ww, wh, S * 0.01)), sw.pane, 0.6);
    layer(cut(rrect(W * x + ww * 0.47, wy, ww * 0.06, wh, 0)), sw.dark, 0.2);
  }
};

/** A wheel part: tyre, hub and spokes, so a spin is plain to see. */
const paintWheel: Painter = ({ W, H, cut, base, layer, sw }) => {
  const cx = W / 2;
  const cy = H / 2;
  const r = Math.min(W, H) * 0.48;
  base(cut(ellipse(cx, cy, r, r, 64)), "#3b322b");
  layer(cut(ellipse(cx, cy, r * 0.64, r * 0.64, 48)), sw.lighter, 0.5);
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2;
    const dx = Math.cos(a);
    const dy = Math.sin(a);
    const w = r * 0.07;
    const pts: Pts = [
      cx - dy * w, cy + dx * w,
      cx + dx * r * 0.6 - dy * w, cy + dy * r * 0.6 + dx * w,
      cx + dx * r * 0.6 + dy * w, cy + dy * r * 0.6 - dx * w,
      cx + dy * w, cy - dx * w,
    ];
    layer(cut(pts, 0.4), sw.dark, 0.2);
  }
  layer(cut(ellipse(cx, cy, r * 0.18, r * 0.18, 24)), sw.deep, 0.4);
};

/** A generic moving piece (a lid, a door): a rounded tab. */
const paintPiece: Painter = ({ W, H, S, cut, base, layer, sw }) => {
  base(cut(rrect(W * 0.02, H * 0.02, W * 0.96, H * 0.96, S * 0.12)), sw.dark);
  layer(cut(rrect(W * 0.14, H * 0.14, W * 0.72, H * 0.3, S * 0.08)), sw.base, 0.4);
};

const PAINTERS: Record<PropSize, Painter> = {
  tiny: paintTiny,
  hand: paintHand,
  carried: paintCarried,
  furniture: paintFurniture,
  vehicle: paintVehicle,
  building: paintBuilding,
};

function toTexture(canvas: HTMLCanvasElement, srgb: boolean): Texture {
  const t = new CanvasTexture(canvas);
  t.colorSpace = srgb ? SRGBColorSpace : NoColorSpace;
  t.wrapS = t.wrapT = ClampToEdgeWrapping;
  t.anisotropy = 4;
  t.needsUpdate = true;
  return t;
}

function paint(spec: PlaceholderSpec): PlaceholderArt {
  if (typeof document === "undefined") throw new Error("placeholder art is browser-only");
  const long = 512;
  const aspect = spec.aspect > 0 ? spec.aspect : 1;
  const W = aspect >= 1 ? long : Math.max(16, Math.round(long * aspect));
  const H = aspect >= 1 ? Math.max(16, Math.round(long / aspect)) : long;
  const sw = swatch(spec.color);

  const colorCanvas = document.createElement("canvas");
  colorCanvas.width = W;
  colorCanvas.height = H;
  const alphaCanvas = document.createElement("canvas");
  alphaCanvas.width = W;
  alphaCanvas.height = H;
  const color = colorCanvas.getContext("2d")!;
  const alpha = alphaCanvas.getContext("2d")!;
  // Opaque colour everywhere: texels outside the silhouette are the darkest
  // tone, so filtering at the cut edge blends into the paper, not black.
  color.fillStyle = sw.dark;
  color.fillRect(0, 0, W, H);
  alpha.fillStyle = "#000";
  alpha.fillRect(0, 0, W, H);

  const kit = makeKit(color, alpha, W, H, sw, rng(hashString(spec.seed)));
  const painter = spec.kind === "wheel" ? paintWheel : spec.kind === "piece" ? paintPiece : PAINTERS[spec.size] ?? paintCarried;
  painter(kit, spec);

  return { map: toTexture(colorCanvas, true), alphaMap: toTexture(alphaCanvas, false) };
}
