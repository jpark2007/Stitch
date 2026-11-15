/**
 * Placeholder art for props with no art yet (`art: null`, CONTRACTS.md
 * decision 8): a cut-paper card in the prop's colour with a simple silhouette,
 * so it still reads as an object.
 *
 * The shape the prop's own words name comes first (./silhouette.ts): a
 * vehicle (bicycle, with its bell if it has one, car, truck, boat, train,
 * plane, wagon, stroller), or a thing held, carried or worn: books, flowers,
 * a letter or postcard, a cup or mug, a hat or cap, a cake (with candles if
 * they are mentioned), a ring, a framed photo, a ball (soccer, baseball,
 * basketball, beach, tennis), a bottle, a key, a suitcase, an umbrella, a
 * guitar, violin or little piano, a lamp or lantern, a basket, a gift, a
 * diploma, a phone (or an old rotary telephone), a camera, a doll or teddy
 * bear, a kite. Otherwise its size class picks:
 *
 *   tiny, hand  a plain paper tag with the prop's name written on it
 *   carried     a small parcel tied with twine, its name on a label
 *   furniture   a small table
 *   vehicle     a car (wheels drawn in unless the prop has wheel parts)
 *   building    a house
 *
 * The tag and the parcel are neutral on purpose: a bouquet with no named
 * shape must not read as a stack of books, so an unknown thing is a keepsake
 * that only says what it is called.
 *
 * Shapes sit on the bottom edge of the card (the default anchor) and fill
 * its middle (the default grip); a shape with a handle on one side (a cup, a
 * key) is mirrored so the handle is on the grip's side.
 *
 * Drawn once on a <canvas> as glued layers of flat paper (no gradients, no
 * outlines), with a hand-cut wobble seeded by the prop id. The colour map is
 * opaque and the silhouette lives in a separate alpha map, so edges never pick
 * up dark fringes. Textures are cached by spec and reference counted. A name
 * is written in the handwriting face (Caveat); when that face is still
 * loading, the card is drawn again once it has.
 *
 * Browser-only.
 */

import { CanvasTexture, ClampToEdgeWrapping, Color, NoColorSpace, SRGBColorSpace, type Texture } from "three";
import type { PropSize } from "./rig";
import type { Silhouette } from "./silhouette";

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
  /** The shape the prop's words name (./silhouette.ts); none: by size. */
  shape?: Silhouette | null;
  /** Which kind of the shape (PlaceholderLook.variant). */
  variant?: string | null;
  /** A bicycle's bell. */
  bell?: boolean;
  /** Wheel centres (image fractions, back to front) where the prop's wheel
   *  parts turn, so a frame meets them; default the shape's own. */
  hubs?: readonly (readonly [number, number])[];
  /** The short name written on a neutral tag or parcel (silhouette.tagLabel). */
  label?: string;
  /** Draw the card mirrored left to right (a handle on the grip's side). */
  mirror?: boolean;
}

export interface PlaceholderArt {
  map: Texture;
  alphaMap: Texture;
}

const DEFAULT_COLOR = "#c4a37a"; // kraft
const INK = new Color("#2f2620");
const CREAM = new Color("#fbf3e4");
const INK_HEX = "#2f2620";
const PAPER = "#f4ecdc";
const TWINE = "#e6d8b8";
const LEAF = "#6f8f55";
const LEAF_DARK = "#56733f";
const FLAME = "#e9963b";
const FLAME_CORE = "#f7d46a";
const GOLD = "#d3a03d";

/* ------------------------------------------------------------------------ */
/* Cache                                                                     */
/* ------------------------------------------------------------------------ */

const cache = new Map<string, { art: PlaceholderArt; refs: number }>();

export function placeholderKey(s: PlaceholderSpec): string {
  const hubs = (s.hubs ?? []).map(([u, v]) => `${u.toFixed(3)},${v.toFixed(3)}`).join(";");
  return [
    s.kind,
    s.size,
    s.color,
    s.aspect.toFixed(3),
    s.seed,
    s.wheels ? 1 : 0,
    s.shape ?? "",
    s.variant ?? "",
    s.bell ? 1 : 0,
    hubs,
    s.label ?? "",
    s.mirror ? 1 : 0,
  ].join("|");
}

/** Get (and hold a reference to) the placeholder textures for a spec. */
export function acquirePlaceholder(spec: PlaceholderSpec): PlaceholderArt {
  const key = placeholderKey(spec);
  const hit = cache.get(key);
  if (hit) {
    hit.refs++;
    return hit.art;
  }
  const art = paint(spec, () => cache.get(key)?.art === art);
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
  /** A detail that stands out from the base: pale on a dark colour, deep on
   *  a pale one. */
  trim: string;
  /** A ribbon: red, or gold on a red prop. */
  ribbon: string;
}

function swatch(color: string): Swatch {
  const base = safeColor(color);
  const mix = (to: Color, k: number) => "#" + base.clone().lerp(to, k).getHexString();
  // Lighter in lightness only, so dark, saturated colours (navy, bottle
  // green) stay themselves instead of greying toward cream.
  const hsl = { h: 0, s: 0, l: 0 };
  base.getHSL(hsl);
  const lift = "#" + new Color().setHSL(hsl.h, hsl.s, Math.min(0.85, hsl.l + 0.09)).getHexString();
  const reddish = hsl.s > 0.3 && (hsl.h < 0.06 || hsl.h > 0.92);
  return {
    base: "#" + base.getHexString(),
    light: lift,
    lighter: mix(CREAM, 0.5),
    dark: mix(INK, 0.22),
    deep: mix(INK, 0.45),
    core: mix(CREAM, 0.7),
    pane: "#" + new Color("#f1e9d6").lerp(base, 0.12).getHexString(),
    trim: hsl.l > 0.68 ? mix(INK, 0.45) : mix(CREAM, 0.6),
    ribbon: reddish ? GOLD : "#a8443a",
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

/** An ellipse turned by `rot` radians (a leaf, a bow loop). */
function ellipseRot(cx: number, cy: number, rx: number, ry: number, rot: number, n = 36): Pts {
  const c = Math.cos(rot);
  const s = Math.sin(rot);
  const pts: Pts = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const x = Math.cos(a) * rx;
    const y = Math.sin(a) * ry;
    pts.push(cx + x * c - y * s, cy + x * s + y * c);
  }
  return pts;
}

/** A segment of an elliptical band from angle a0 to a1: the outer edge one
 *  way, the inner edge back, so the middle stays open (a ring, a handle). */
function band(
  cx: number,
  cy: number,
  rxOut: number,
  ryOut: number,
  rxIn: number,
  ryIn: number,
  a0: number,
  a1: number,
  inDy = 0,
  n = 24,
): Pts {
  const pts: Pts = [];
  for (let i = 0; i <= n; i++) {
    const a = a0 + ((a1 - a0) * i) / n;
    pts.push(cx + Math.cos(a) * rxOut, cy + Math.sin(a) * ryOut);
  }
  for (let i = n; i >= 0; i--) {
    const a = a0 + ((a1 - a0) * i) / n;
    pts.push(cx + Math.cos(a) * rxIn, cy + inDy + Math.sin(a) * ryIn);
  }
  return pts;
}

/** A rectangle turned by `rot` radians about its centre (a tied-on label). */
function rotRect(cx: number, cy: number, w: number, h: number, rot: number): Pts {
  const c = Math.cos(rot);
  const s = Math.sin(rot);
  const out: Pts = [];
  for (const [x, y] of [
    [-w / 2, -h / 2],
    [w / 2, -h / 2],
    [w / 2, h / 2],
    [-w / 2, h / 2],
  ]) {
    out.push(cx + x * c - y * s, cy + x * s + y * c);
  }
  return out;
}

/** A flower head: a scalloped disc with `petals` round petals. */
function bloom(cx: number, cy: number, r: number, petals: number, phase: number): Pts {
  const pts: Pts = [];
  const n = petals * 10;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const rr = r * (0.74 + 0.26 * Math.abs(Math.cos((a * petals) / 2 + phase)));
    pts.push(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr);
  }
  return pts;
}

/** A regular polygon (a soccer ball's patch). */
function ngon(cx: number, cy: number, r: number, sides: number, rot: number): Pts {
  const pts: Pts = [];
  for (let i = 0; i < sides; i++) {
    const a = rot + (i / sides) * Math.PI * 2;
    pts.push(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
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
/* Handwriting                                                               */
/* ------------------------------------------------------------------------ */

/** The handwriting face (next/font's Caveat, as on the crafting page's name
 *  slips), with system hands behind it. */
function handFont(): string {
  const fallback = '"Bradley Hand", "Segoe Print", cursive';
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue("--font-caveat").trim();
    return v ? `${v}, ${fallback}` : fallback;
  } catch {
    return fallback;
  }
}

/** Write `text` in hand, centred on (cx, cy), as large as fits the box; on
 *  two lines when that writes it larger. */
function writeHand(ctx: CanvasRenderingContext2D, text: string, cx: number, cy: number, maxW: number, maxH: number, rot: number) {
  const font = handFont();
  const fitSize = (lines: string[], cap: number) => {
    let size = cap;
    ctx.font = `700 ${size}px ${font}`;
    const widest = () => Math.max(...lines.map((l) => ctx.measureText(l).width));
    while (size > 8 && widest() > maxW) {
      size *= 0.94;
      ctx.font = `700 ${size}px ${font}`;
    }
    return size;
  };
  const lead = 0.86;
  let lines = [text];
  let size = fitSize(lines, maxH * 0.86);
  const gap = text.indexOf(" ");
  if (gap > 0) {
    // Two lines, broken at the space nearest the middle, when that is larger.
    let best = gap;
    for (let i = gap; i >= 0; i = text.indexOf(" ", i + 1)) {
      if (Math.abs(i - text.length / 2) < Math.abs(best - text.length / 2)) best = i;
    }
    const two = [text.slice(0, best), text.slice(best + 1)];
    const size2 = fitSize(two, maxH / (1 + lead));
    if (size2 > size * 1.08) {
      lines = two;
      size = size2;
    }
  }
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(rot);
  ctx.font = `700 ${size}px ${font}`;
  ctx.fillStyle = INK_HEX;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const step = size * lead;
  lines.forEach((l, i) => ctx.fillText(l, 0, (i - (lines.length - 1) / 2) * step + size * 0.04));
  ctx.restore();
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
  /** Write a name in hand, in ink, as large as fits (cx, cy, max w, max h). */
  write: (text: string, cx: number, cy: number, maxW: number, maxH: number, rot?: number) => void;
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
    write: (text, cx, cy, maxW, maxH, rot = 0) => writeHand(color, text, cx, cy, maxW, maxH, rot),
  };
}

type Painter = (k: Kit, spec: PlaceholderSpec) => void;

/** The box a shape is drawn in: as near its own proportions (`pref`, width /
 *  height) as the card allows within `slack`, standing on the card's bottom
 *  edge (the default anchor) and centred across it. */
interface Box {
  w: number;
  h: number;
  /** Short side. */
  s: number;
  X: (u: number) => number;
  Y: (v: number) => number;
  /** Box fractions to a polygon, densified for the wobble. */
  P: (...uv: number[]) => Pts;
  /** A rounded rectangle from (u0, v0) to (u1, v1), radius in px. */
  R: (u0: number, v0: number, u1: number, v1: number, r?: number, tl?: number, tr?: number, br?: number, bl?: number) => Pts;
  /** An ellipse at (u, v), radii as fractions of the box width and height. */
  E: (u: number, v: number, ru: number, rv: number, n?: number) => Pts;
  /** A circle at (u, v), radius a fraction of the short side. */
  C: (u: number, v: number, r: number, n?: number) => Pts;
  /** A bar from (u0, v0) to (u1, v1), `t` (fraction of the short side) wide. */
  L: (u0: number, v0: number, u1: number, v1: number, t: number) => Pts;
}

function fit(k: Kit, pref: number, slack = 1.35): Box {
  const a = k.W / k.H;
  const t = Math.min(pref * slack, Math.max(pref / slack, a));
  const w = t >= a ? k.W : k.H * t;
  const h = t >= a ? k.W / t : k.H;
  const x = (k.W - w) / 2;
  const y = k.H - h;
  const s = Math.min(w, h);
  const X = (u: number) => x + w * u;
  const Y = (v: number) => y + h * v;
  return {
    w,
    h,
    s,
    X,
    Y,
    P: (...uv) => densify(uv.map((f, i) => (i % 2 ? Y(f) : X(f))), Math.max(3, s / 40)),
    R: (u0, v0, u1, v1, r = s * 0.03, tl = r, tr = r, br = r, bl = r) => rrect(X(u0), Y(v0), w * (u1 - u0), h * (v1 - v0), r, tl, tr, br, bl),
    E: (u, v, ru, rv, n = 48) => ellipse(X(u), Y(v), w * ru, h * rv, n),
    C: (u, v, r, n = 40) => ellipse(X(u), Y(v), s * r, s * r, n),
    L: (u0, v0, u1, v1, t) => capsule(X(u0), Y(v0), X(u1), Y(v1), Math.max(2, s * t)),
  };
}

/** A whole elliptical ring, cut as two overlapping halves so its middle is
 *  open: the back half first, the front half over it. */
function paintRing(k: Kit, cx: number, cy: number, rx: number, ry: number, rxIn: number, ryIn: number, inDy: number, back: string, front: string) {
  k.base(k.cut(band(cx, cy, rx, ry, rxIn, ryIn, Math.PI - 0.12, Math.PI * 2 + 0.12, inDy), 0.6), back);
  k.base(k.cut(band(cx, cy, rx, ry, rxIn, ryIn, -0.02, Math.PI + 0.02, inDy), 0.6), front, 0.3);
}

/* ---------------------------- size-class shapes -------------------------- */

/** A plain paper tag on a twine loop, the prop's name written on a pale slip:
 *  a keepsake that does not pretend to be any one thing. Upright on a tall or
 *  square card, lying sideways (the hole to the left) on a wide one. */
const paintTag: Painter = (k, spec) => {
  const { W, H, S, cut, base, layer, sw, write, rand } = k;
  const label = spec.label || "keepsake";
  const twine = Math.max(2, S * 0.026);
  if (W / H < 1.15) {
    const hx = W * 0.5;
    const hy = H * 0.24;
    base(cut(capsule(hx, hy, W * 0.38, H * 0.03, twine), 0.4), TWINE);
    base(cut(capsule(hx, hy, W * 0.6, H * 0.025, twine), 0.4), TWINE);
    base(cut(capsule(W * 0.38, H * 0.03, W * 0.6, H * 0.025, twine), 0.4), TWINE);
    const body = [W * 0.08, H * 0.3, W * 0.3, H * 0.12, W * 0.7, H * 0.12, W * 0.92, H * 0.3, W * 0.92, H * 0.94, W * 0.88, H * 0.985, W * 0.12, H * 0.985, W * 0.08, H * 0.94];
    base(cut(densify(body, S / 40)), sw.base);
    layer(cut(ellipse(hx, hy, S * 0.07, S * 0.07, 28)), sw.lighter, 0.4);
    layer(cut(ellipse(hx, hy, S * 0.032, S * 0.032, 20), 0.3), sw.deep, 0.2);
    layer(cut(capsule(hx, hy, W * 0.44, H * 0.12, twine * 0.9), 0.3), TWINE, 0.3);
    const rot = (rand() - 0.5) * 0.06;
    layer(cut(densify(rotRect(W * 0.5, H * 0.645, W * 0.76, H * 0.56, rot), S / 40)), PAPER, 0.5);
    write(label, W * 0.5, H * 0.645, W * 0.7, H * 0.5, rot);
  } else {
    const hx = W * 0.13;
    const hy = H * 0.5;
    base(cut(capsule(hx, hy, W * 0.03, H * 0.05, twine), 0.4), TWINE);
    base(cut(capsule(hx, hy, W * 0.1, H * 0.03, twine), 0.4), TWINE);
    base(cut(capsule(W * 0.03, H * 0.05, W * 0.1, H * 0.03, twine), 0.4), TWINE);
    const body = [W * 0.03, H * 0.34, W * 0.17, H * 0.1, W * 0.95, H * 0.1, W * 0.98, H * 0.16, W * 0.98, H * 0.86, W * 0.95, H * 0.92, W * 0.17, H * 0.92, W * 0.03, H * 0.68];
    base(cut(densify(body, S / 40)), sw.base);
    layer(cut(ellipse(hx, hy, S * 0.1, S * 0.1, 28)), sw.lighter, 0.4);
    layer(cut(ellipse(hx, hy, S * 0.045, S * 0.045, 20), 0.3), sw.deep, 0.2);
    const rot = (rand() - 0.5) * 0.05;
    layer(cut(densify(rotRect(W * 0.605, H * 0.51, W * 0.7, H * 0.68, rot), S / 40)), PAPER, 0.5);
    write(label, W * 0.605, H * 0.51, W * 0.65, H * 0.6, rot);
  }
};

/** A small parcel wrapped in the prop's colour, tied with twine, its name on
 *  a pale label: the neutral carried thing. */
const paintParcel: Painter = (k, spec) => {
  const { cut, base, layer, sw, write, rand } = k;
  const b = fit(k, 1.3, 1.6);
  const tw = 0.04;
  // The bow sits on top, in the middle (where a hand holding it by the twine
  // would be).
  base(cut(densify(ellipseRot(b.X(0.43), b.Y(0.12), b.w * 0.08, b.h * 0.05, -0.5), b.s / 40), 0.5), TWINE);
  base(cut(densify(ellipseRot(b.X(0.57), b.Y(0.12), b.w * 0.08, b.h * 0.05, 0.5), b.s / 40), 0.5), TWINE);
  base(cut(b.R(0.04, 0.2, 0.96, 0.99, b.s * 0.04)), sw.base);
  layer(cut(b.R(0.04, 0.2, 0.96, 0.31, b.s * 0.04, b.s * 0.04, b.s * 0.04, 0, 0)), sw.light, 0.3);
  layer(cut(b.L(0.5, 0.21, 0.5, 0.985, tw), 0.4), TWINE, 0.3);
  layer(cut(b.L(0.05, 0.52, 0.95, 0.52, tw), 0.4), TWINE, 0.3);
  base(cut(b.C(0.5, 0.17, 0.045, 20), 0.4), TWINE, 0.4);
  const rot = -0.05 + (rand() - 0.5) * 0.04;
  const [cx, cy] = [b.X(0.5), b.Y(0.63)];
  layer(cut(densify(rotRect(cx, cy, b.w * 0.8, b.h * 0.5, rot), b.s / 40)), PAPER, 0.6);
  write(spec.label || "keepsake", cx, cy, b.w * 0.74, b.h * 0.45, rot);
};

/** A small stack lying flat, spines out: books. */
const paintBooks: Painter = ({ W, H, cut, base, layer, sw, rand }) => {
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

/* ------------------------------------------------------------------------ */
/* Shapes the prop's words name (./silhouette.ts)                            */
/* ------------------------------------------------------------------------ */

/** A bar of paper from (x0, y0) to (x1, y1), `w` wide, with round ends. */
function capsule(x0: number, y0: number, x1: number, y1: number, w: number): Pts {
  const a = Math.atan2(y1 - y0, x1 - x0);
  const r = w / 2;
  const pts: Pts = [];
  const cap = (cx: number, cy: number, from: number) => {
    for (let i = 0; i <= 8; i++) {
      const t = from + (i / 8) * Math.PI;
      pts.push(cx + Math.cos(t) * r, cy + Math.sin(t) * r);
    }
  };
  cap(x1, y1, a - Math.PI / 2);
  cap(x0, y0, a + Math.PI / 2);
  return pts;
}

/** Pixel wheel centres, back to front: the prop's wheel parts, else `fallback`. */
function hubsPx(k: Kit, spec: PlaceholderSpec, fallback: readonly (readonly [number, number])[]): [number, number][] {
  const src = spec.hubs && spec.hubs.length > 0 ? spec.hubs : fallback;
  return [...src].sort((a, b) => a[0] - b[0]).map(([u, v]) => [k.W * u, k.H * v]);
}

/** Tyres drawn into the body (a prop without wheel parts). */
function paintTyres(k: Kit, hubs: readonly (readonly [number, number])[], r: number) {
  for (const [cx, cy] of hubs) {
    k.base(k.cut(ellipse(cx, cy, r, r)), "#3b322b");
    k.layer(k.cut(ellipse(cx, cy, r * 0.62, r * 0.62)), k.sw.lighter, 0.4);
    k.layer(k.cut(ellipse(cx, cy, r * 0.18, r * 0.18, 24)), k.sw.deep, 0.4);
  }
}

const BIKE_HUBS = [
  [0.2, 0.82],
  [0.8, 0.82],
] as const;

/** A bicycle, facing right: diamond frame between the wheels, saddle,
 *  handlebars, and a bell on the bars when it has one. */
const paintBicycle: Painter = (k, spec) => {
  const { W, H, cut, base, layer, sw } = k;
  const [[rx, ry], [fx, fy]] = hubsPx(k, spec, BIKE_HUBS);
  const r = H * VEHICLE_WHEEL_RADIUS;
  const at = (u: number, dv: number): [number, number] => [rx + (fx - rx) * u, (ry + fy) / 2 + H * dv];
  const tube = Math.max(3, H * 0.036);
  const [bx, by] = at(0.46, 0.01); // bottom bracket
  const [cx, cy] = at(0.36, -0.21); // seat cluster
  const [sx, sy] = at(0.33, -0.28); // under the saddle
  const [tx, ty] = at(0.84, -0.25); // head tube, top
  const [hx, hy] = at(0.87, -0.14); // head tube, bottom
  const [gx, gy] = at(0.82, -0.4); // handlebar
  const bar = (x0: number, y0: number, x1: number, y1: number, w = tube, fill = sw.base) =>
    base(cut(capsule(x0, y0, x1, y1, w), 0.6), fill);
  if (spec.wheels) paintTyres(k, [[rx, ry], [fx, fy]], r);
  bar(bx, by, rx, ry); // chain stay
  bar(cx, cy, rx, ry); // seat stay
  bar(bx, by, sx, sy); // seat tube
  bar(cx, cy, tx, ty); // top tube
  bar(bx, by, hx, hy); // down tube
  bar(tx, ty, hx, hy, tube * 1.25); // head tube
  bar(hx, hy, fx, fy, tube * 0.9, sw.dark); // fork
  bar(tx, ty, gx, gy, tube * 0.8, sw.dark); // stem
  bar(gx, gy, gx - W * 0.1, gy + H * 0.04, tube * 0.8, sw.dark); // bars
  layer(cut(capsule(gx - W * 0.1, gy + H * 0.04, gx - W * 0.06, gy + H * 0.025, tube * 1.3), 0.5), sw.deep, 0.4); // grip
  // Saddle on its post.
  base(cut(rrect(sx - W * 0.085, sy - H * 0.045, W * 0.15, H * 0.05, H * 0.025, H * 0.025, H * 0.012, H * 0.02, H * 0.012)), sw.deep);
  // Chainring and crank.
  base(cut(ellipse(bx, by, H * 0.06, H * 0.06, 32)), sw.dark);
  layer(cut(ellipse(bx, by, H * 0.03, H * 0.03, 24)), sw.core, 0.4);
  layer(cut(capsule(bx, by, bx + H * 0.05, by + H * 0.07, tube * 0.7), 0.4), sw.deep, 0.3);
  // Mudguard over the back wheel.
  layer(cut(densify(arc(rx, ry, r * 1.12, r * 1.28, Math.PI * 1.05, Math.PI * 1.6), 4), 0.5), sw.light, 0.4);
  if (spec.bell) {
    const bellX = gx - W * 0.035;
    const bellY = gy - H * 0.035;
    const br = H * 0.04;
    base(cut(ellipse(bellX, bellY, br, br * 0.85, 32)), "#c9c6bd"); // silver
    layer(cut(ellipse(bellX - br * 0.3, bellY - br * 0.3, br * 0.35, br * 0.25, 16), 0.3), "#f1efe8", 0.3);
  }
};

/** A ring segment from angle a0 to a1 (radians), inner and outer radius. */
function arc(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number, n = 16): Pts {
  const pts: Pts = [];
  for (let i = 0; i <= n; i++) {
    const a = a0 + ((a1 - a0) * i) / n;
    pts.push(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
  }
  for (let i = n; i >= 0; i--) {
    const a = a0 + ((a1 - a0) * i) / n;
    pts.push(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
  }
  return pts;
}

/** Wheel arches and, without wheel parts, tyres, for the boxy shapes. */
function wheelsFor(k: Kit, spec: PlaceholderSpec, scale = 1) {
  const hubs = hubsPx(k, spec, VEHICLE_WHEELS);
  const r = k.H * VEHICLE_WHEEL_RADIUS * scale;
  for (const [cx, cy] of hubs) k.layer(k.cut(ellipse(cx, cy, r * 1.18, r * 1.18)), k.sw.deep, 0.3);
  if (spec.wheels) paintTyres(k, hubs, r);
}

/** A pickup truck, facing right: cab and an open bed. */
const paintTruck: Painter = (k, spec) => {
  const { W, H, cut, base, layer, sw } = k;
  base(cut(rrect(W * 0.03, H * 0.42, W * 0.6, H * 0.4, H * 0.04)), sw.base);
  base(cut(rrect(W * 0.6, H * 0.12, W * 0.26, H * 0.7, H * 0.08, H * 0.08, H * 0.02, H * 0.04, H * 0.02)), sw.base);
  base(cut(rrect(W * 0.8, H * 0.4, W * 0.18, H * 0.42, H * 0.1, H * 0.02, H * 0.06, H * 0.06, H * 0.02)), sw.base);
  layer(cut(rrect(W * 0.64, H * 0.18, W * 0.18, H * 0.2, H * 0.03)), sw.pane, 0.6);
  layer(cut(rrect(W * 0.06, H * 0.44, W * 0.54, H * 0.06, 2)), sw.dark, 0.4); // bed rail
  layer(cut(rrect(W * 0.02, H * 0.74, W * 0.96, H * 0.06, H * 0.03)), sw.deep, 0.5);
  layer(cut(rrect(W * 0.93, H * 0.46, W * 0.05, H * 0.07, H * 0.03)), sw.core, 0.4);
  wheelsFor(k, spec);
};

/** A sailing boat: hull, mast and sail. */
const paintBoat: Painter = ({ W, H, S, cut, base, layer, sw }) => {
  base(cut(rrect(W * 0.46, H * 0.05, S * 0.035, H * 0.55, 2)), sw.deep);
  base(cut(densify([W * 0.5, H * 0.08, W * 0.86, H * 0.52, W * 0.5, H * 0.52], 6)), sw.lighter);
  base(cut(densify([W * 0.02, H * 0.56, W * 0.98, H * 0.52, W * 0.84, H * 0.94, W * 0.14, H * 0.94], 6)), sw.base);
  layer(cut(densify([W * 0.04, H * 0.6, W * 0.96, H * 0.56, W * 0.94, H * 0.63, W * 0.06, H * 0.67], 6)), sw.light, 0.4);
  for (const u of [0.3, 0.46, 0.62]) layer(cut(ellipse(W * u, H * 0.76, S * 0.035, S * 0.035, 20)), sw.pane, 0.5);
};

/** A steam locomotive, facing right: cab, boiler, chimney. */
const paintTrain: Painter = (k, spec) => {
  const { W, H, cut, base, layer, sw } = k;
  base(cut(rrect(W * 0.7, H * 0.12, W * 0.1, H * 0.3, 2)), sw.deep);
  base(cut(rrect(W * 0.3, H * 0.36, W * 0.6, H * 0.4, H * 0.18, H * 0.04, H * 0.18, H * 0.18, H * 0.04)), sw.base);
  base(cut(rrect(W * 0.04, H * 0.1, W * 0.32, H * 0.68, H * 0.03)), sw.dark);
  base(cut(densify([W * 0.88, H * 0.6, W * 0.99, H * 0.84, W * 0.86, H * 0.84], 4)), sw.deep);
  layer(cut(rrect(W * 0.02, H * 0.06, W * 0.36, H * 0.07, H * 0.03)), sw.deep, 0.5); // roof
  layer(cut(rrect(W * 0.1, H * 0.2, W * 0.18, H * 0.2, H * 0.02)), sw.pane, 0.6);
  for (const u of [0.48, 0.66]) layer(cut(rrect(W * u, H * 0.36, W * 0.03, H * 0.4, 1)), sw.light, 0.3);
  layer(cut(rrect(W * 0.02, H * 0.74, W * 0.9, H * 0.06, H * 0.03)), sw.deep, 0.5);
  wheelsFor(k, spec);
};

/** An aeroplane, facing right: fuselage, wing, tail fin and windows. */
const paintPlane: Painter = ({ W, H, cut, base, layer, sw }) => {
  base(cut(densify([W * 0.04, H * 0.46, W * 0.17, H * 0.46, W * 0.13, H * 0.12, W * 0.04, H * 0.12], 4)), sw.dark);
  base(cut(capsule(W * 0.1, H * 0.5, W * 0.9, H * 0.5, H * 0.22)), sw.base);
  base(cut(densify([W * 0.4, H * 0.54, W * 0.6, H * 0.54, W * 0.5, H * 0.9, W * 0.4, H * 0.9], 4)), sw.dark);
  layer(cut(rrect(W * 0.84, H * 0.42, W * 0.08, H * 0.08, H * 0.04)), sw.pane, 0.6); // cockpit
  for (let i = 0; i < 6; i++) layer(cut(ellipse(W * (0.26 + i * 0.09), H * 0.46, H * 0.03, H * 0.035, 16), 0.3), sw.pane, 0.4);
  layer(cut(rrect(W * 0.12, H * 0.54, W * 0.76, H * 0.04, 2)), sw.light, 0.3);
};

/** A little pull-wagon: open box, wheels and a long handle. */
const paintWagon: Painter = (k, spec) => {
  const { W, H, cut, base, layer, sw } = k;
  base(cut(capsule(W * 0.8, H * 0.6, W * 0.97, H * 0.1, Math.max(3, H * 0.04))), sw.deep);
  base(cut(rrect(W * 0.93, H * 0.06, W * 0.06, H * 0.08, H * 0.03)), sw.deep);
  base(cut(rrect(W * 0.04, H * 0.36, W * 0.78, H * 0.38, H * 0.04)), sw.base);
  layer(cut(rrect(W * 0.04, H * 0.36, W * 0.78, H * 0.07, H * 0.03)), sw.light, 0.4);
  for (const u of [0.24, 0.43, 0.62]) layer(cut(rrect(W * u, H * 0.46, W * 0.02, H * 0.22, 1)), sw.dark, 0.25);
  wheelsFor(k, spec, 0.85);
};

/** A pram: deep basket, folding hood, push handle behind. */
const paintStroller: Painter = (k, spec) => {
  const { W, H, cut, base, layer, sw } = k;
  base(cut(capsule(W * 0.24, H * 0.46, W * 0.06, H * 0.12, Math.max(3, H * 0.035))), sw.deep);
  base(cut(capsule(W * 0.02, H * 0.12, W * 0.12, H * 0.1, Math.max(4, H * 0.06))), sw.deep);
  base(cut(ellipse(W * 0.52, H * 0.5, W * 0.32, H * 0.24)), sw.base);
  const hood: Pts = [W * 0.52, H * 0.5];
  for (let i = 0; i <= 16; i++) {
    const a = Math.PI + (i / 16) * Math.PI * 0.62;
    hood.push(W * 0.52 + Math.cos(a) * W * 0.33, H * 0.5 + Math.sin(a) * H * 0.42);
  }
  base(cut(densify(hood, 6)), sw.dark);
  for (const f of [0.25, 0.5]) {
    const a = Math.PI + f * Math.PI * 0.62;
    layer(cut(capsule(W * 0.52, H * 0.5, W * 0.52 + Math.cos(a) * W * 0.32, H * 0.5 + Math.sin(a) * H * 0.41, H * 0.02), 0.4), sw.deep, 0.3);
  }
  layer(cut(rrect(W * 0.22, H * 0.5, W * 0.62, H * 0.05, 2)), sw.light, 0.4);
  wheelsFor(k, spec, 0.8);
};

/* ------------------------- things held and carried ------------------------ */

/** A bouquet: blooms in the prop's colour over green stems and leaves,
 *  gathered in a paper cone tied with a ribbon (where a hand holds it). */
const paintFlowers: Painter = (k) => {
  const { cut, base, layer, sw, rand } = k;
  const b = fit(k, 0.78, 1.45);
  const heads: [number, number, number][] = [
    [0.5, 0.15, 0.17],
    [0.27, 0.27, 0.15],
    [0.73, 0.27, 0.15],
    [0.38, 0.4, 0.14],
    [0.62, 0.4, 0.14],
    [0.15, 0.44, 0.11],
    [0.85, 0.44, 0.11],
  ];
  for (const [u, v] of heads) base(cut(b.L(0.5, 0.78, u, v, 0.03), 0.4), LEAF_DARK);
  base(cut(densify(ellipseRot(b.X(0.27), b.Y(0.55), b.s * 0.16, b.s * 0.055, -0.6), b.s / 40), 0.5), LEAF);
  base(cut(densify(ellipseRot(b.X(0.73), b.Y(0.55), b.s * 0.16, b.s * 0.055, 0.6), b.s / 40), 0.5), LEAF);
  const tones = [sw.base, sw.light, sw.dark];
  heads.forEach(([u, v, r], i) => {
    const R = b.s * r;
    base(cut(bloom(b.X(u), b.Y(v), R, 5 + (i % 2), rand() * Math.PI), 0.5), tones[i % tones.length], i > 2 ? 0.4 : 0);
    layer(cut(ellipse(b.X(u), b.Y(v), R * 0.36, R * 0.36, 20), 0.4), sw.lighter, 0.3);
    layer(cut(ellipse(b.X(u), b.Y(v), R * 0.14, R * 0.14, 12), 0.3), sw.deep, 0.2);
  });
  // The paper cone, its top edge cut in points.
  base(cut(b.P(0.14, 0.5, 0.32, 0.6, 0.5, 0.5, 0.68, 0.6, 0.86, 0.5, 0.57, 0.995, 0.43, 0.995)), PAPER, 0.7);
  layer(cut(b.P(0.5, 0.5, 0.68, 0.6, 0.57, 0.995, 0.5, 0.995)), sw.core, 0.15);
  layer(cut(b.R(0.37, 0.73, 0.63, 0.79, b.s * 0.02)), sw.ribbon, 0.5);
  base(cut(b.P(0.48, 0.77, 0.4, 0.9, 0.36, 0.87), 0.4), sw.ribbon, 0.4);
  base(cut(b.P(0.52, 0.77, 0.64, 0.87, 0.6, 0.9), 0.4), sw.ribbon, 0.4);
};

/** A letter: an envelope with its flap down, a red seal and a stamp; a
 *  postcard: a stamp, an address and a few lines of writing. */
const paintLetter: Painter = (k, spec) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 1.5, 1.5);
  base(cut(b.R(0.03, 0.08, 0.97, 0.99, b.s * 0.03)), sw.base);
  if (spec.variant === "postcard") {
    layer(cut(b.L(0.5, 0.2, 0.5, 0.88, 0.018), 0.3), sw.dark, 0.1);
    for (const v of [0.52, 0.65, 0.78]) layer(cut(b.L(0.56, v, 0.9, v, 0.018), 0.3), sw.dark, 0.1);
    for (const [v, e] of [
      [0.32, 0.42],
      [0.44, 0.38],
      [0.56, 0.42],
      [0.68, 0.3],
    ]) {
      layer(cut(b.L(0.1, v, e, v - 0.01, 0.022), 0.4), sw.deep, 0.1);
    }
    layer(cut(b.R(0.75, 0.15, 0.91, 0.42, 2)), sw.core, 0.4);
    layer(cut(b.R(0.775, 0.19, 0.885, 0.38, 2)), sw.ribbon, 0.2);
    return;
  }
  layer(cut(b.P(0.03, 0.99, 0.5, 0.52, 0.97, 0.99)), sw.light, 0.3);
  layer(cut(b.P(0.03, 0.09, 0.97, 0.09, 0.5, 0.62)), sw.dark, 0.7);
  layer(cut(b.C(0.5, 0.6, 0.075, 24)), "#a8443a", 0.5);
  layer(cut(b.C(0.5, 0.6, 0.04, 16), 0.3), "#c25b4f", 0.2);
  layer(cut(b.R(0.78, 0.15, 0.92, 0.36, 2)), sw.core, 0.4);
  layer(cut(b.R(0.8, 0.18, 0.9, 0.33, 2)), sw.deep, 0.2);
};

/** A teacup on its saucer, or a mug; the handle on the right (mirrored to
 *  the grip's side). Steam over a hot cup. */
const paintCup: Painter = (k, spec) => {
  const { cut, base, layer, sw } = k;
  if (spec.variant === "mug") {
    const b = fit(k, 1.05, 1.35);
    base(cut(band(b.X(0.74), b.Y(0.54), b.w * 0.2, b.h * 0.22, b.w * 0.1, b.h * 0.12, -Math.PI / 2 - 0.1, Math.PI / 2 + 0.1), 0.5), sw.dark);
    base(cut(b.R(0.1, 0.14, 0.76, 0.99, b.s * 0.07, b.s * 0.02, b.s * 0.02, b.s * 0.1, b.s * 0.1)), sw.base);
    layer(cut(b.E(0.43, 0.165, 0.31, 0.05)), sw.deep, 0.3);
    layer(cut(b.R(0.1, 0.66, 0.76, 0.75, 0)), sw.light, 0.3);
    layer(cut(b.R(0.16, 0.24, 0.22, 0.6, b.s * 0.02)), sw.lighter, 0.1);
    return;
  }
  const b = fit(k, 1.2, 1.4);
  // Steam, the handle, the bowl over its foot, the saucer under it all.
  base(cut(b.L(0.42, 0.06, 0.38, 0.2, 0.025), 0.4), sw.core);
  base(cut(b.L(0.55, 0.02, 0.58, 0.18, 0.025), 0.4), sw.core);
  base(cut(b.E(0.5, 0.92, 0.49, 0.075)), sw.dark);
  layer(cut(b.E(0.5, 0.905, 0.33, 0.04)), sw.light, 0.2);
  base(cut(band(b.X(0.8), b.Y(0.5), b.w * 0.14, b.h * 0.17, b.w * 0.07, b.h * 0.09, -Math.PI / 2 - 0.2, Math.PI / 2), 0.5), sw.base);
  const bowl: Pts = [];
  for (let i = 0; i <= 24; i++) {
    const a = (i / 24) * Math.PI;
    bowl.push(b.X(0.5 + Math.cos(a) * 0.36), b.Y(0.3 + Math.sin(a) * 0.54));
  }
  base(cut(densify(bowl, b.s / 40)), sw.base, 0.3);
  layer(cut(b.E(0.5, 0.3, 0.36, 0.06)), sw.deep, 0.3);
  layer(cut(b.E(0.5, 0.31, 0.3, 0.035)), sw.dark, 0.1);
  layer(cut(b.R(0.16, 0.4, 0.84, 0.46, 0)), sw.lighter, 0.2);
  layer(cut(b.C(0.5, 0.6, 0.06, 20)), sw.lighter, 0.2);
};

/** A brimmed hat in three-quarter view (crown, band, the brim's front edge
 *  over the crown's foot), or a cap with its peak to the right. */
const paintHat: Painter = (k, spec) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 1.6, 1.4);
  if (spec.variant === "cap") {
    const dome: Pts = [];
    for (let i = 0; i <= 24; i++) {
      const a = Math.PI + (i / 24) * Math.PI;
      dome.push(b.X(0.42 + Math.cos(a) * 0.36), b.Y(0.84 + Math.sin(a) * 0.74));
    }
    base(cut(densify(dome, b.s / 40)), sw.base);
    base(cut(densify(ellipseRot(b.X(0.76), b.Y(0.84), b.w * 0.23, b.h * 0.09, 0.06), b.s / 40)), sw.dark, 0.5);
    for (const u of [0.24, 0.6]) layer(cut(b.L(0.42, 0.12, u, 0.84, 0.02), 0.3), sw.dark, 0.1);
    layer(cut(b.R(0.06, 0.76, 0.78, 0.86, 0)), sw.dark, 0.3);
    layer(cut(b.C(0.42, 0.11, 0.06, 16)), sw.deep, 0.3);
    return;
  }
  base(cut(b.E(0.5, 0.82, 0.49, 0.16)), sw.dark);
  base(cut(b.R(0.24, 0.12, 0.76, 0.84, 0, b.w * 0.12, b.w * 0.12, 0, 0)), sw.base, 0.3);
  layer(cut(b.P(0.36, 0.13, 0.5, 0.2, 0.64, 0.13)), sw.dark, 0.2);
  layer(cut(b.R(0.24, 0.6, 0.76, 0.72, 0)), sw.deep, 0.4);
  layer(cut(b.C(0.7, 0.66, 0.07, 20)), sw.trim, 0.4);
  base(cut(band(b.X(0.5), b.Y(0.82), b.w * 0.49, b.h * 0.16, b.w * 0.28, b.h * 0.05, -0.05, Math.PI + 0.05, -b.h * 0.005)), sw.dark, 0.5);
  layer(cut(band(b.X(0.5), b.Y(0.82), b.w * 0.47, b.h * 0.14, b.w * 0.4, b.h * 0.1, 0.25, Math.PI - 0.25)), sw.light, 0.1);
};

/** A cake on a plate: a layer of sponge, dripping icing, cherries on top,
 *  or candles with their flames when the words mention them. */
const paintCake: Painter = (k, spec) => {
  const { cut, base, layer, sw } = k;
  const candles = spec.variant === "candles";
  const b = fit(k, 1.1, 1.35);
  const top = candles ? 0.44 : 0.3;
  if (candles) {
    for (const [u, tone] of [
      [0.32, sw.trim],
      [0.5, sw.ribbon],
      [0.68, sw.trim],
    ] as const) {
      base(cut(b.R(u - 0.025, top - 0.25, u + 0.025, top + 0.02, 2)), tone);
      base(cut(densify(ellipseRot(b.X(u), b.Y(top - 0.31), b.s * 0.035, b.s * 0.065, 0), b.s / 40), 0.3), FLAME);
      layer(cut(ellipse(b.X(u), b.Y(top - 0.295), b.s * 0.016, b.s * 0.032, 12), 0.2), FLAME_CORE, 0.1);
    }
  }
  base(cut(b.E(0.5, 0.935, 0.5, 0.06)), PAPER);
  base(cut(b.R(0.1, top, 0.9, 0.93, b.s * 0.03)), sw.base, 0.3);
  layer(cut(b.R(0.1, (top + 0.93) / 2 + 0.06, 0.9, (top + 0.93) / 2 + 0.11, 0)), sw.dark, 0.2);
  const icing: Pts = [b.X(0.08), b.Y(top - 0.04), b.X(0.92), b.Y(top - 0.04)];
  for (let i = 0; i <= 40; i++) {
    const u = 0.92 - (0.84 * i) / 40;
    const drip = Math.max(0, Math.sin((i / 40) * Math.PI * 5 + 0.6));
    icing.push(b.X(u), b.Y(top + 0.06 + 0.1 * drip * drip));
  }
  base(cut(densify(icing, b.s / 40), 0.6), "#fbf3e4", 0.4);
  if (!candles) for (const u of [0.3, 0.5, 0.7]) base(cut(b.C(u, top - 0.07, 0.06, 20)), "#a8443a", 0.4);
};

/** A ring in three-quarter view, its middle open, with a stone on top unless
 *  the words make it a plain band. */
const paintRingShape: Painter = (k, spec) => {
  const gem = spec.variant === "gem";
  const b = fit(k, 1, 1.25);
  const cy = gem ? 0.64 : 0.56;
  const rv = gem ? 0.33 : 0.42;
  paintRing(k, b.X(0.5), b.Y(cy), b.w * 0.44, b.h * rv, b.w * 0.33, b.h * rv * 0.66, b.h * 0.03, k.sw.dark, k.sw.base);
  k.layer(k.cut(band(b.X(0.5), b.Y(cy), b.w * 0.42, b.h * rv * 0.95, b.w * 0.38, b.h * rv * 0.84, 0.5, 1.4), 0.3), k.sw.lighter, 0.1);
  if (!gem) return;
  k.base(k.cut(b.P(0.4, 0.34, 0.6, 0.34, 0.56, 0.24, 0.44, 0.24)), k.sw.deep);
  k.base(k.cut(b.P(0.34, 0.15, 0.41, 0.06, 0.59, 0.06, 0.66, 0.15, 0.5, 0.31), 0.5), "#e3ecee", 0.4);
  k.layer(k.cut(b.P(0.41, 0.07, 0.5, 0.15, 0.35, 0.15), 0.3), "#ffffff", 0.1);
  k.layer(k.cut(b.P(0.5, 0.15, 0.66, 0.15, 0.5, 0.3), 0.3), "#c3d2d6", 0.1);
};

/** A framed photograph: frame, pale mat, and a little portrait in it. */
const paintPhoto: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 0.8, 1.4);
  base(cut(b.R(0.03, 0.02, 0.97, 0.99, b.s * 0.03)), sw.base);
  layer(cut(b.R(0.1, 0.08, 0.9, 0.93, b.s * 0.01)), sw.dark, 0.3);
  layer(cut(b.R(0.13, 0.11, 0.87, 0.9, 0)), PAPER, 0.4);
  layer(cut(b.R(0.2, 0.18, 0.8, 0.82, 0)), sw.pane, 0.3);
  const shoulders: Pts = [];
  for (let i = 0; i <= 20; i++) {
    const a = Math.PI + (i / 20) * Math.PI;
    shoulders.push(b.X(0.5 + Math.cos(a) * 0.24), b.Y(0.82 + Math.sin(a) * 0.2));
  }
  layer(cut(densify(shoulders, b.s / 40), 0.5), sw.deep, 0.1);
  layer(cut(b.E(0.5, 0.47, 0.11, 0.1)), sw.deep, 0.1);
};

/** A ball standing on the card's foot: plain with a band, or patched like a
 *  soccer ball, seamed like a baseball or tennis ball, lined like a
 *  basketball, striped like a beach ball. */
const paintBall: Painter = (k, spec) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 1, 1);
  const r = b.s * 0.48;
  const cx = b.X(0.5);
  const cy = b.Y(1) - r - b.s * 0.015;
  base(cut(ellipse(cx, cy, r, r, 64)), sw.base);
  const seam = (side: -1 | 1, color: string, width: number, stitches: boolean) => {
    const ox = cx + side * r * 1.18;
    const rr = r * 0.78;
    const a0 = side < 0 ? -0.95 : Math.PI - 0.95;
    layer(cut(arc(ox, cy, rr - width / 2, rr + width / 2, a0, a0 + 1.9, 24), 0.3), color, 0.15);
    if (!stitches) return;
    for (let i = 1; i < 8; i++) {
      const a = a0 + (1.9 * i) / 8;
      const [x, y] = [ox + Math.cos(a) * rr, cy + Math.sin(a) * rr];
      const t = r * 0.06;
      layer(cut(capsule(x - Math.cos(a + 0.7) * t, y - Math.sin(a + 0.7) * t, x + Math.cos(a + 0.7) * t, y + Math.sin(a + 0.7) * t, r * 0.025), 0.2), color, 0.1);
    }
  };
  switch (spec.variant) {
    case "soccer":
      layer(cut(ngon(cx, cy, r * 0.27, 5, -Math.PI / 2), 0.4), sw.deep, 0.2);
      for (let i = 0; i < 5; i++) {
        const a = -Math.PI / 2 + Math.PI / 5 + (i * 2 * Math.PI) / 5;
        layer(cut(ngon(cx + Math.cos(a) * r * 0.86, cy + Math.sin(a) * r * 0.86, r * 0.25, 5, a + Math.PI), 0.4), sw.deep, 0.2);
        const b0 = -Math.PI / 2 + (i * 2 * Math.PI) / 5;
        layer(cut(capsule(cx + Math.cos(b0) * r * 0.27, cy + Math.sin(b0) * r * 0.27, cx + Math.cos(b0) * r * 0.62, cy + Math.sin(b0) * r * 0.62, r * 0.03), 0.2), sw.dark, 0.1);
      }
      break;
    case "baseball":
      seam(-1, "#b0473b", r * 0.035, true);
      seam(1, "#b0473b", r * 0.035, true);
      break;
    case "tennis":
      seam(-1, "#fbf3e4", r * 0.07, false);
      seam(1, "#fbf3e4", r * 0.07, false);
      break;
    case "basketball":
      layer(cut(capsule(cx - r, cy, cx + r, cy, r * 0.045), 0.3), sw.deep, 0.1);
      layer(cut(capsule(cx, cy - r, cx, cy + r, r * 0.045), 0.3), sw.deep, 0.1);
      layer(cut(arc(cx - r * 1.35, cy, r * 1.0, r * 1.05, -0.75, 0.75, 24), 0.3), sw.deep, 0.1);
      layer(cut(arc(cx + r * 1.35, cy, r * 1.0, r * 1.05, Math.PI - 0.75, Math.PI + 0.75, 24), 0.3), sw.deep, 0.1);
      break;
    case "beach":
      layer(cut(ellipse(cx, cy, r * 0.8, r, 48)), "#fbf3e4", 0.2);
      layer(cut(ellipse(cx, cy, r * 0.52, r, 48)), sw.ribbon, 0.2);
      layer(cut(ellipse(cx, cy, r * 0.22, r, 32)), "#fbf3e4", 0.2);
      layer(cut(ellipse(cx, cy - r * 0.86, r * 0.18, r * 0.1, 20)), sw.lighter, 0.2);
      break;
    default:
      layer(cut(rrect(cx - r, cy - r * 0.13, r * 2, r * 0.26, 0)), sw.trim, 0.2);
  }
  layer(cut(densify(ellipseRot(cx - r * 0.42, cy - r * 0.46, r * 0.16, r * 0.08, -0.7), 4), 0.3), sw.lighter, 0.1);
};

/** A bottle: cap, neck, shoulders, body and a pale label. */
const paintBottle: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 0.42, 1.4);
  base(cut(b.R(0.37, 0.05, 0.63, 0.32, b.s * 0.02)), sw.base);
  base(cut(b.P(0.37, 0.27, 0.63, 0.27, 0.86, 0.43, 0.14, 0.43)), sw.base);
  base(cut(b.R(0.14, 0.4, 0.86, 0.995, b.s * 0.1, b.s * 0.05, b.s * 0.05, b.s * 0.14, b.s * 0.14)), sw.base);
  base(cut(b.R(0.33, 0.0, 0.67, 0.09, b.s * 0.03)), sw.deep, 0.3);
  layer(cut(b.R(0.14, 0.56, 0.86, 0.82, 0)), PAPER, 0.4);
  layer(cut(b.R(0.14, 0.6, 0.86, 0.63, 0)), sw.dark, 0.1);
  layer(cut(b.R(0.14, 0.75, 0.86, 0.78, 0)), sw.dark, 0.1);
  layer(cut(b.R(0.22, 0.45, 0.28, 0.54, b.s * 0.02)), sw.lighter, 0.1);
  layer(cut(b.R(0.22, 0.84, 0.28, 0.94, b.s * 0.02)), sw.lighter, 0.1);
};

/** An old door key lying on its side: an open bow on the left (mirrored to
 *  the grip's side), a shaft, and the bit with its teeth. */
const paintKey: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 2.5, 1.4);
  const R = b.h * 0.4;
  const bx = b.X(0) + R + b.h * 0.04;
  const by = b.Y(0.46);
  base(cut(capsule(bx + R * 0.7, by, b.X(0.97), by, b.h * 0.17), 0.5), sw.base);
  base(cut(b.R(0.77, 0.46, 0.84, 0.8, 1)), sw.base);
  base(cut(b.R(0.88, 0.46, 0.95, 0.9, 1)), sw.base);
  base(cut(b.R(0.83, 0.46, 0.885, 0.68, 1)), sw.base);
  paintRing(k, bx, by, R, R, R * 0.52, R * 0.52, 0, sw.base, sw.base);
  layer(cut(rrect(bx + R * 0.92, by - b.h * 0.15, b.w * 0.035, b.h * 0.3, 2)), sw.dark, 0.3);
  layer(cut(rrect(bx + R * 0.92 + b.w * 0.06, by - b.h * 0.12, b.w * 0.025, b.h * 0.24, 2)), sw.dark, 0.3);
  layer(cut(capsule(bx + R * 1.3, by - b.h * 0.04, b.X(0.92), by - b.h * 0.04, b.h * 0.04), 0.3), sw.lighter, 0.1);
};

/** A suitcase: handle on top (where it is carried), two straps with buckles,
 *  a travel sticker and corner patches. */
const paintSuitcase: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 1.3, 1.45);
  const bar = 0.075;
  base(cut(b.L(0.36, 0.06, 0.64, 0.06, bar)), sw.deep);
  base(cut(b.L(0.36, 0.06, 0.36, 0.24, bar)), sw.deep);
  base(cut(b.L(0.64, 0.06, 0.64, 0.24, bar)), sw.deep);
  base(cut(b.R(0.03, 0.22, 0.97, 0.99, b.s * 0.08)), sw.base);
  layer(cut(b.R(0.03, 0.5, 0.97, 0.54, 0)), sw.dark, 0.2);
  for (const u of [0.22, 0.78]) {
    layer(cut(b.R(u - 0.04, 0.22, u + 0.04, 0.99, 0)), sw.dark, 0.4);
    layer(cut(b.R(u - 0.055, 0.42, u + 0.055, 0.5, b.s * 0.01)), sw.lighter, 0.4);
  }
  layer(cut(b.C(0.5, 0.74, 0.12, 28)), sw.trim, 0.4);
  layer(cut(b.C(0.5, 0.74, 0.07, 20), 0.3), sw.ribbon, 0.2);
  layer(cut(densify(rotRect(b.X(0.38), b.Y(0.34), b.w * 0.12, b.h * 0.1, -0.2), 4)), PAPER, 0.3);
  for (const u of [0.03, 0.87]) layer(cut(b.R(u, 0.88, u + 0.1, 0.99, b.s * 0.04)), sw.deep, 0.4);
};

/** An open umbrella: scalloped canopy with alternate panels, a tip, the
 *  shaft and a crook handle at the foot. */
const paintUmbrella: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 0.95, 1.4);
  const rim = 0.52;
  const canopy: Pts = [];
  for (let i = 0; i <= 32; i++) {
    const a = Math.PI + (i / 32) * Math.PI;
    canopy.push(b.X(0.5 + Math.cos(a) * 0.49), b.Y(rim + Math.sin(a) * 0.45));
  }
  for (let s = 0; s < 4; s++) {
    for (let i = 1; i <= 8; i++) {
      const t = i / 8;
      canopy.push(b.X(0.99 - 0.245 * (s + t)), b.Y(rim - 0.07 * Math.sin(t * Math.PI)));
    }
  }
  base(cut(b.L(0.5, 0.02, 0.5, 0.1, 0.035)), sw.deep);
  base(cut(densify(canopy, b.s / 40)), sw.base);
  layer(cut(b.P(0.5, 0.07, 0.255, rim + 0.02, 0.255, 0.7, 0.5, 0.7)), sw.light, 0.1);
  layer(cut(b.P(0.5, 0.07, 0.745, rim + 0.02, 0.745, 0.7, 1, 0.7, 1, 0)), sw.light, 0.1);
  for (const u of [0.01, 0.255, 0.5, 0.745, 0.99]) layer(cut(b.L(0.5, 0.07, u, rim, 0.014), 0.3), sw.dark, 0.1);
  base(cut(b.L(0.5, rim - 0.02, 0.5, 0.86, 0.035)), sw.deep);
  const r = b.w * 0.07;
  const t = b.s * 0.035;
  base(cut(arc(b.X(0.5) - r, b.Y(0.86), r - t / 2, r + t / 2, 0, Math.PI, 16), 0.4), sw.deep);
};

/** An acoustic guitar standing up: headstock and pegs, neck, the two bouts,
 *  sound hole, bridge and strings. */
const paintGuitar: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 0.42, 1.4);
  for (const v of [0.025, 0.07]) for (const u of [0.3, 0.7]) base(cut(b.C(u, v, 0.05, 12), 0.3), sw.dark);
  base(cut(b.R(0.36, 0.0, 0.64, 0.1, b.s * 0.04)), sw.deep);
  base(cut(b.R(0.43, 0.08, 0.57, 0.5, 0)), sw.dark);
  base(cut(b.E(0.5, 0.56, 0.36, 0.14)), sw.base);
  base(cut(b.E(0.5, 0.8, 0.48, 0.195)), sw.base);
  layer(cut(b.R(0.44, 0.08, 0.56, 0.58, 0)), sw.deep, 0.2);
  layer(cut(b.C(0.5, 0.62, 0.19, 28)), sw.lighter, 0.2);
  layer(cut(b.C(0.5, 0.62, 0.14, 28)), sw.deep, 0.3);
  layer(cut(b.R(0.32, 0.81, 0.68, 0.845, 2)), sw.deep, 0.4);
  for (const u of [0.465, 0.49, 0.51, 0.535]) layer(cut(b.L(u, 0.05, u, 0.83, 0.012), 0.1), sw.core, 0.05);
};

/** A violin standing up: scroll, pegbox, neck, the waisted body, f-holes,
 *  bridge and tailpiece. */
const paintViolin: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 0.4, 1.4);
  base(cut(b.C(0.5, 0.045, 0.11, 20)), sw.dark);
  base(cut(b.R(0.43, 0.05, 0.57, 0.16, 2)), sw.dark);
  base(cut(b.R(0.45, 0.14, 0.55, 0.36, 0)), sw.base);
  base(cut(b.E(0.5, 0.46, 0.36, 0.13)), sw.base);
  base(cut(b.E(0.5, 0.78, 0.45, 0.175)), sw.base);
  base(cut(b.R(0.28, 0.48, 0.72, 0.76, 0)), sw.base);
  layer(cut(b.C(0.5, 0.045, 0.05, 14)), sw.deep, 0.2);
  layer(cut(b.R(0.455, 0.14, 0.545, 0.62, b.s * 0.02)), sw.deep, 0.3);
  layer(cut(b.L(0.34, 0.6, 0.37, 0.73, 0.03), 0.2), sw.deep, 0.1);
  layer(cut(b.L(0.66, 0.6, 0.63, 0.73, 0.03), 0.2), sw.deep, 0.1);
  layer(cut(b.R(0.38, 0.68, 0.62, 0.7, 1)), sw.core, 0.3);
  layer(cut(b.P(0.43, 0.78, 0.57, 0.78, 0.545, 0.92, 0.455, 0.92)), sw.deep, 0.3);
};

/** A small upright piano: lid, front panel, music on its stand, the keys
 *  (black keys in twos and threes) and two legs. */
const paintPiano: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 1.2, 1.4);
  for (const u of [0.08, 0.85]) base(cut(b.R(u, 0.85, u + 0.07, 0.995, 2)), sw.deep);
  base(cut(b.R(0.04, 0.1, 0.96, 0.88, b.s * 0.02)), sw.base);
  base(cut(b.R(0.01, 0.04, 0.99, 0.12, b.s * 0.02)), sw.dark, 0.3);
  layer(cut(b.R(0.12, 0.18, 0.88, 0.44, b.s * 0.01)), sw.dark, 0.2);
  layer(cut(b.R(0.32, 0.22, 0.68, 0.38, 1)), PAPER, 0.3);
  for (const v of [0.27, 0.31, 0.35]) layer(cut(b.L(0.35, v, 0.65, v, 0.008), 0.1), sw.deep, 0.05);
  layer(cut(b.R(0.06, 0.5, 0.94, 0.66, 1)), "#f7f1e3", 0.4);
  const n = 14;
  for (let i = 1; i < n; i++) layer(cut(b.L(0.06 + (0.88 * i) / n, 0.51, 0.06 + (0.88 * i) / n, 0.65, 0.008), 0.1), "#cbbfa8", 0.05);
  const black = [1, 1, 0, 1, 1, 1, 0];
  for (let i = 0; i < n - 1; i++) {
    if (!black[i % 7]) continue;
    const u = 0.06 + (0.88 * (i + 1)) / n;
    layer(cut(b.R(u - 0.02, 0.5, u + 0.02, 0.59, 1), 0.2), INK_HEX, 0.2);
  }
  layer(cut(b.R(0.04, 0.66, 0.96, 0.7, 0)), sw.dark, 0.3);
};

/** A table lamp (shade, stem, foot), or a lantern on its ring with a flame
 *  behind warm glass. */
const paintLamp: Painter = (k, spec) => {
  const { cut, base, layer, sw } = k;
  if (spec.variant === "lantern") {
    const b = fit(k, 0.62, 1.4);
    base(cut(band(b.X(0.5), b.Y(0.15), b.w * 0.2, b.h * 0.14, b.w * 0.13, b.h * 0.09, Math.PI, Math.PI * 2), 0.4), sw.deep);
    base(cut(b.R(0.44, 0.1, 0.56, 0.16, 2)), sw.deep);
    base(cut(b.P(0.3, 0.15, 0.7, 0.15, 0.84, 0.27, 0.16, 0.27)), sw.base);
    base(cut(b.R(0.19, 0.26, 0.81, 0.85, 0)), "#f3d98f");
    layer(cut(b.E(0.5, 0.58, 0.24, 0.2)), "#fbeab5", 0.1);
    layer(cut(densify(ellipseRot(b.X(0.5), b.Y(0.58), b.s * 0.07, b.s * 0.15, 0), 4), 0.3), FLAME, 0.1);
    layer(cut(densify(ellipseRot(b.X(0.5), b.Y(0.61), b.s * 0.035, b.s * 0.08, 0), 4), 0.2), FLAME_CORE, 0.1);
    for (const u of [0.19, 0.77]) layer(cut(b.R(u, 0.26, u + 0.04, 0.85, 0)), sw.base, 0.3);
    base(cut(b.R(0.13, 0.84, 0.87, 0.99, b.s * 0.03)), sw.base, 0.3);
    layer(cut(b.R(0.13, 0.84, 0.87, 0.88, 0)), sw.dark, 0.2);
    return;
  }
  const b = fit(k, 0.72, 1.4);
  base(cut(b.L(0.5, 0.44, 0.5, 0.9, 0.06)), sw.deep);
  base(cut(b.E(0.5, 0.72, 0.17, 0.13)), sw.dark);
  base(cut(b.R(0.24, 0.89, 0.76, 0.99, b.s * 0.04)), sw.dark);
  layer(cut(b.E(0.5, 0.47, 0.3, 0.035)), "#f6d98a", 0.1);
  base(cut(b.P(0.24, 0.04, 0.76, 0.04, 0.94, 0.47, 0.06, 0.47)), sw.base, 0.4);
  layer(cut(b.P(0.06, 0.42, 0.94, 0.42, 0.94, 0.47, 0.06, 0.47)), sw.trim, 0.2);
  layer(cut(b.P(0.24, 0.04, 0.76, 0.04, 0.765, 0.07, 0.235, 0.07)), sw.dark, 0.1);
  layer(cut(b.C(0.62, 0.72, 0.03, 12)), sw.lighter, 0.1);
};

/** A woven basket: an arched handle over it (where it is carried), a cloth
 *  peeking out, the weave in bands and ribs. */
const paintBasket: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 1.3, 1.45);
  base(cut(band(b.X(0.5), b.Y(0.5), b.w * 0.38, b.h * 0.48, b.w * 0.38 - b.s * 0.07, b.h * 0.48 - b.s * 0.07, Math.PI - 0.05, Math.PI * 2 + 0.05), 0.5), sw.dark);
  base(cut(b.P(0.2, 0.48, 0.32, 0.34, 0.46, 0.48)), PAPER);
  base(cut(b.P(0.56, 0.48, 0.7, 0.37, 0.8, 0.48)), sw.ribbon);
  base(cut(b.P(0.03, 0.46, 0.97, 0.46, 0.86, 0.99, 0.14, 0.99)), sw.base, 0.3);
  for (const v of [0.6, 0.72, 0.84]) layer(cut(b.R(0.0, v, 1.0, v + 0.05, 0)), sw.light, 0.15);
  for (let i = 1; i < 8; i++) {
    const u = 0.1 + (0.8 * i) / 8;
    layer(cut(b.L(u, 0.5, 0.5 + (u - 0.5) * 0.85, 0.98, 0.02), 0.2), sw.dark, 0.1);
  }
  base(cut(b.L(0.03, 0.47, 0.97, 0.47, 0.08)), sw.dark, 0.4);
};

/** A wrapped gift: a box with its lid, a ribbon down the front and a bow. */
const paintGift: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 1.0, 1.4);
  base(cut(densify(ellipseRot(b.X(0.38), b.Y(0.17), b.w * 0.15, b.h * 0.085, -0.45), b.s / 40)), sw.ribbon);
  base(cut(densify(ellipseRot(b.X(0.62), b.Y(0.17), b.w * 0.15, b.h * 0.085, 0.45), b.s / 40)), sw.ribbon);
  layer(cut(ellipseRot(b.X(0.4), b.Y(0.18), b.w * 0.07, b.h * 0.035, -0.45, 20), 0.3), sw.deep, 0.1);
  layer(cut(ellipseRot(b.X(0.6), b.Y(0.18), b.w * 0.07, b.h * 0.035, 0.45, 20), 0.3), sw.deep, 0.1);
  base(cut(b.R(0.08, 0.36, 0.92, 0.99, b.s * 0.02)), sw.base);
  base(cut(b.R(0.04, 0.25, 0.96, 0.4, b.s * 0.02)), sw.light, 0.5);
  layer(cut(b.R(0.44, 0.25, 0.56, 0.99, 0)), sw.ribbon, 0.3);
  base(cut(b.C(0.5, 0.24, 0.06, 18)), sw.ribbon, 0.4);
};

/** A diploma: a rolled scroll with curled ends, tied with a ribbon and a
 *  gold seal. */
const paintDiploma: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 2.3, 1.45);
  base(cut(b.R(0.06, 0.24, 0.94, 0.8, b.s * 0.02)), sw.base);
  layer(cut(b.R(0.06, 0.3, 0.94, 0.36, 0)), sw.light, 0.1);
  for (const u of [0.06, 0.94]) {
    base(cut(b.E(u, 0.52, 0.05, 0.29)), sw.dark, 0.3);
    layer(cut(b.E(u, 0.52, 0.025, 0.15)), sw.deep, 0.2);
    layer(cut(b.E(u, 0.52, 0.008, 0.06, 12)), sw.dark, 0.1);
  }
  layer(cut(b.R(0.45, 0.24, 0.53, 0.8, 0)), sw.ribbon, 0.3);
  base(cut(b.P(0.47, 0.72, 0.5, 0.72, 0.44, 0.99, 0.4, 0.94)), sw.ribbon, 0.4);
  base(cut(b.P(0.5, 0.72, 0.53, 0.72, 0.6, 0.94, 0.56, 0.99)), sw.ribbon, 0.4);
  base(cut(b.C(0.49, 0.54, 0.16, 24)), GOLD, 0.5);
  layer(cut(b.C(0.49, 0.54, 0.1, 20), 0.3), "#e7bd5c", 0.2);
};

/** A phone: a smartphone with a dark screen, or (rotary) an old desk
 *  telephone with its handset on top and a dial. */
const paintPhone: Painter = (k, spec) => {
  const { cut, base, layer, sw } = k;
  if (spec.variant === "rotary") {
    const b = fit(k, 1.25, 1.4);
    base(cut(b.P(0.17, 0.36, 0.83, 0.36, 0.97, 0.99, 0.03, 0.99)), sw.base);
    base(cut(b.L(0.14, 0.24, 0.86, 0.24, 0.13)), sw.dark, 0.3);
    for (const u of [0.04, 0.74]) base(cut(b.R(u, 0.18, u + 0.22, 0.4, b.s * 0.06)), sw.dark, 0.4);
    const [cx, cy, r] = [b.X(0.5), b.Y(0.69), b.s * 0.24];
    layer(cut(ellipse(cx, cy, r, r, 40)), PAPER, 0.5);
    for (let i = 0; i < 10; i++) {
      const a = -Math.PI * 0.35 + (i / 10) * Math.PI * 1.6;
      layer(cut(ellipse(cx + Math.cos(a) * r * 0.68, cy + Math.sin(a) * r * 0.68, r * 0.13, r * 0.13, 14), 0.2), sw.dark, 0.1);
    }
    layer(cut(ellipse(cx, cy, r * 0.32, r * 0.32, 24)), sw.base, 0.2);
    return;
  }
  const b = fit(k, 0.52, 1.4);
  base(cut(b.R(0.04, 0.01, 0.96, 0.99, b.s * 0.16)), sw.base);
  layer(cut(b.R(0.11, 0.08, 0.89, 0.87, b.s * 0.04)), "#34404a", 0.3);
  layer(cut(b.P(0.11, 0.6, 0.89, 0.26, 0.89, 0.4, 0.11, 0.74)), "#3f4c57", 0.05);
  for (const v of [0.18, 0.34]) for (const u of [0.22, 0.44, 0.66]) layer(cut(b.R(u, v, u + 0.14, v + 0.07, b.s * 0.02), 0.2), sw.lighter, 0.1);
  layer(cut(b.L(0.4, 0.045, 0.6, 0.045, 0.03), 0.2), sw.dark, 0.1);
  layer(cut(b.C(0.5, 0.93, 0.07, 16)), sw.dark, 0.2);
};

/** A camera: viewfinder hump, shutter button, leather band and a big lens. */
const paintCamera: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 1.45, 1.4);
  base(cut(b.P(0.33, 0.26, 0.38, 0.08, 0.62, 0.08, 0.67, 0.26)), sw.dark);
  base(cut(b.R(0.73, 0.1, 0.87, 0.24, b.s * 0.02)), sw.deep);
  base(cut(b.R(0.03, 0.22, 0.97, 0.98, b.s * 0.08)), sw.base);
  layer(cut(b.R(0.03, 0.44, 0.97, 0.82, 0)), sw.dark, 0.2);
  layer(cut(b.R(0.08, 0.28, 0.22, 0.38, b.s * 0.02)), sw.lighter, 0.3);
  const [cx, cy, r] = [b.X(0.5), b.Y(0.6), b.s * 0.33];
  layer(cut(ellipse(cx, cy, r, r, 48)), sw.deep, 0.6);
  layer(cut(ellipse(cx, cy, r * 0.74, r * 0.74, 40)), "#3c4752", 0.3);
  layer(cut(ellipse(cx, cy, r * 0.42, r * 0.42, 32)), "#56636e", 0.2);
  layer(cut(ellipse(cx - r * 0.28, cy - r * 0.3, r * 0.14, r * 0.1, 16), 0.2), "#e9eef0", 0.1);
};

/** A teddy bear sitting up, or a rag doll in a dress. */
const paintDoll: Painter = (k, spec) => {
  const { cut, base, layer, sw } = k;
  if (spec.variant === "teddy") {
    const b = fit(k, 0.85, 1.35);
    for (const u of [0.28, 0.72]) {
      base(cut(b.C(u, 0.1, 0.11, 24)), sw.dark);
      layer(cut(b.C(u, 0.1, 0.055, 16)), sw.lighter, 0.2);
    }
    for (const u of [0.31, 0.69]) base(cut(b.E(u, 0.88, 0.16, 0.11)), sw.base);
    base(cut(densify(ellipseRot(b.X(0.17), b.Y(0.56), b.w * 0.09, b.h * 0.17, 0.5), b.s / 40)), sw.dark);
    base(cut(densify(ellipseRot(b.X(0.83), b.Y(0.56), b.w * 0.09, b.h * 0.17, -0.5), b.s / 40)), sw.dark);
    base(cut(b.E(0.5, 0.64, 0.31, 0.27)), sw.base, 0.3);
    layer(cut(b.E(0.5, 0.68, 0.17, 0.15)), sw.lighter, 0.2);
    for (const u of [0.31, 0.69]) layer(cut(b.E(u, 0.9, 0.08, 0.06)), sw.lighter, 0.2);
    base(cut(b.E(0.5, 0.27, 0.25, 0.2)), sw.base, 0.4);
    layer(cut(b.E(0.5, 0.33, 0.11, 0.075)), sw.lighter, 0.2);
    layer(cut(b.E(0.5, 0.31, 0.04, 0.025, 16), 0.2), INK_HEX, 0.1);
    for (const u of [0.41, 0.59]) layer(cut(b.C(u, 0.23, 0.028, 12), 0.2), INK_HEX, 0.1);
    layer(cut(b.P(0.4, 0.42, 0.5, 0.47, 0.4, 0.52), 0.3), sw.ribbon, 0.2);
    layer(cut(b.P(0.6, 0.42, 0.5, 0.47, 0.6, 0.52), 0.3), sw.ribbon, 0.2);
    return;
  }
  const b = fit(k, 0.55, 1.35);
  const skin = "#f0d9bf";
  const hair = "#6b4a35";
  base(cut(b.E(0.5, 0.2, 0.36, 0.17)), hair);
  for (const u of [0.4, 0.6]) {
    base(cut(b.L(u, 0.7, u, 0.95, 0.12)), skin);
    base(cut(b.E(u, 0.965, 0.1, 0.03)), sw.deep, 0.3);
  }
  base(cut(b.L(0.32, 0.42, 0.14, 0.64, 0.12)), skin);
  base(cut(b.L(0.68, 0.42, 0.86, 0.64, 0.12)), skin);
  base(cut(b.P(0.36, 0.37, 0.64, 0.37, 0.9, 0.8, 0.1, 0.8)), sw.base, 0.3);
  layer(cut(b.P(0.36, 0.37, 0.64, 0.37, 0.58, 0.44, 0.42, 0.44)), PAPER, 0.2);
  layer(cut(b.R(0.1, 0.74, 0.9, 0.8, 0)), sw.trim, 0.2);
  base(cut(b.E(0.5, 0.21, 0.27, 0.13)), skin, 0.3);
  layer(cut(b.P(0.24, 0.17, 0.32, 0.08, 0.5, 0.06, 0.68, 0.08, 0.76, 0.17, 0.6, 0.13, 0.5, 0.16, 0.4, 0.13)), hair, 0.2);
  for (const u of [0.4, 0.6]) {
    layer(cut(b.C(u, 0.21, 0.04, 12), 0.2), INK_HEX, 0.1);
    layer(cut(b.C(u + (u < 0.5 ? -0.06 : 0.06), 0.26, 0.05, 12), 0.2), "#e7a59a", 0.05);
  }
  layer(cut(band(b.X(0.5), b.Y(0.25), b.w * 0.07, b.h * 0.03, b.w * 0.05, b.h * 0.015, 0.3, Math.PI - 0.3), 0.1), INK_HEX, 0.05);
};

/** A kite: a diamond with two paler panels, crossed spars, and a tail of
 *  bows below. */
const paintKite: Painter = (k) => {
  const { cut, base, layer, sw } = k;
  const b = fit(k, 0.72, 1.4);
  const tail = [0.5, 0.74, 0.58, 0.82, 0.45, 0.9, 0.55, 0.98];
  for (let i = 0; i < tail.length - 2; i += 2) base(cut(b.L(tail[i], tail[i + 1], tail[i + 2], tail[i + 3], 0.02), 0.3), sw.dark);
  base(cut(b.P(0.5, 0.02, 0.9, 0.33, 0.5, 0.75, 0.1, 0.33)), sw.base);
  layer(cut(b.P(0.5, 0.02, 0.9, 0.33, 0.5, 0.33)), sw.light, 0.1);
  layer(cut(b.P(0.5, 0.75, 0.1, 0.33, 0.5, 0.33)), sw.light, 0.1);
  layer(cut(b.L(0.5, 0.04, 0.5, 0.73, 0.022), 0.2), sw.deep, 0.1);
  layer(cut(b.L(0.12, 0.33, 0.88, 0.33, 0.022), 0.2), sw.deep, 0.1);
  for (let i = 2; i < tail.length; i += 2) {
    const [u, v] = [tail[i], tail[i + 1]];
    base(cut(b.P(u, v, u - 0.08, v - 0.03, u - 0.08, v + 0.03), 0.3), sw.ribbon, 0.3);
    base(cut(b.P(u, v, u + 0.08, v - 0.03, u + 0.08, v + 0.03), 0.3), sw.ribbon, 0.3);
  }
};

const SHAPE_PAINTERS: Record<Silhouette, Painter> = {
  bicycle: paintBicycle,
  car: paintVehicle,
  truck: paintTruck,
  boat: paintBoat,
  train: paintTrain,
  plane: paintPlane,
  wagon: paintWagon,
  stroller: paintStroller,
  books: paintBooks,
  flowers: paintFlowers,
  letter: paintLetter,
  cup: paintCup,
  hat: paintHat,
  cake: paintCake,
  ring: paintRingShape,
  photo: paintPhoto,
  ball: paintBall,
  bottle: paintBottle,
  key: paintKey,
  suitcase: paintSuitcase,
  umbrella: paintUmbrella,
  guitar: paintGuitar,
  violin: paintViolin,
  piano: paintPiano,
  lamp: paintLamp,
  basket: paintBasket,
  gift: paintGift,
  diploma: paintDiploma,
  phone: paintPhone,
  camera: paintCamera,
  doll: paintDoll,
  kite: paintKite,
};

const PAINTERS: Record<PropSize, Painter> = {
  tiny: paintTag,
  hand: paintTag,
  carried: paintParcel,
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

function painterFor(spec: PlaceholderSpec): Painter {
  if (spec.kind === "wheel") return paintWheel;
  if (spec.kind === "piece") return paintPiece;
  return (spec.shape ? SHAPE_PAINTERS[spec.shape] : PAINTERS[spec.size]) ?? paintParcel;
}

/** Paint a spec's card. `alive` says whether the art is still in use, for a
 *  card drawn again once the handwriting face has loaded. */
function paint(spec: PlaceholderSpec, alive: () => boolean): PlaceholderArt {
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
  const painter = painterFor(spec);

  const draw = () => {
    color.setTransform(1, 0, 0, 1, 0, 0);
    alpha.setTransform(1, 0, 0, 1, 0, 0);
    // Opaque colour everywhere: texels outside the silhouette are the darkest
    // tone, so filtering at the cut edge blends into the paper, not black.
    color.fillStyle = sw.dark;
    color.fillRect(0, 0, W, H);
    alpha.fillStyle = "#000";
    alpha.fillRect(0, 0, W, H);
    if (spec.mirror) {
      color.setTransform(-1, 0, 0, 1, W, 0);
      alpha.setTransform(-1, 0, 0, 1, W, 0);
    }
    painter(makeKit(color, alpha, W, H, sw, rng(hashString(spec.seed))), spec);
    color.setTransform(1, 0, 0, 1, 0, 0);
    alpha.setTransform(1, 0, 0, 1, 0, 0);
  };
  draw();

  const art = { map: toTexture(colorCanvas, true), alphaMap: toTexture(alphaCanvas, false) };
  // A name in hand: if the face is still loading, write it again once it has.
  if (painter === paintTag || painter === paintParcel) {
    const font = `700 48px ${handFont()}`;
    const fonts = typeof document.fonts === "undefined" ? null : document.fonts;
    if (fonts && !fonts.check(font)) {
      fonts
        .load(font)
        .then(() => {
          if (!alive()) return;
          draw();
          art.map.needsUpdate = true;
        })
        .catch(() => {
          /* keep the system hand */
        });
    }
  }
  return art;
}
