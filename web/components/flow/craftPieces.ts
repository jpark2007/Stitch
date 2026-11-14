// The hand-made paper pieces of the crafting book (CraftStage.tsx): cut-out
// specs drawn with the stage's own cutout kit (components/stage/cutout.ts),
// so the set layers, the props and the waiting note are card like the real
// pop-ups, and small canvas prints (storyboard frames, name slips, ticks)
// drawn with the book's paper and inks (components/book/paperArt.ts).
//
// Browser-only. Every piece is cached by key and shared, never disposed per
// piece: the stage builds a handful at most.
import { CanvasTexture, SRGBColorSpace, type Texture } from "three";

import { ACCENT, INK, INK_SOFT, paper, rng, type PageFonts } from "@/components/book/paperArt";
import { getCutout, type Cutout, type CutoutSpec, type PaintKit } from "@/components/stage/cutout";
import { PALETTE } from "@/components/stage/art";
import type { PropChip } from "@/lib/types";

type Pt = [number, number];

function poly(pts: Pt[]) {
  const p = new Path2D();
  p.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) p.lineTo(pts[i][0], pts[i][1]);
  p.closePath();
  return p;
}

function rect(x0: number, y0: number, x1: number, y1: number) {
  return poly([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]);
}

function roundRect(x0: number, y0: number, x1: number, y1: number, r: number) {
  const p = new Path2D();
  p.roundRect(x0, y0, x1 - x0, y1 - y0, r);
  return p;
}

function circle(cx: number, cy: number, r: number, into = new Path2D()) {
  into.moveTo(cx + r, cy);
  into.arc(cx, cy, r, 0, Math.PI * 2);
  return into;
}

/** A rolling line from x0 to x1 through the crests, closed below the fold. */
function rolling(x0: number, x1: number, base: number, crests: Pt[]) {
  const p = new Path2D();
  p.moveTo(x0, -0.05);
  p.lineTo(x0, base);
  let px = x0;
  let py = base;
  for (const [x, y] of crests) {
    const mx = (px + x) / 2;
    p.bezierCurveTo(mx, py, mx, y, x, y);
    px = x;
    py = y;
  }
  p.bezierCurveTo((px + x1) / 2, py, (px + x1) / 2, base, x1, base);
  p.lineTo(x1, -0.05);
  p.closePath();
  return p;
}

/** A pencilled stroke from point to point (colour pass only). */
function pencil(k: PaintKit, pts: Pt[], width = 0.012, color = "rgba(70,58,48,0.42)") {
  const p = new Path2D();
  p.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) p.lineTo(pts[i][0], pts[i][1]);
  k.line(p, color, width);
}

// ---------------------------------------------------------------------------
// The set: three layers of card, back to front, in the set palette, still
// carrying the pencil lines they were cut along (a set being built, not the
// finished one: the real scene takes their place).

/** Height and depth (stage units) of each backdrop layer, back first. */
export const LAYERS = [
  { height: 0.95, z: 0.02, aspect: 2.2 },
  { height: 0.5, z: 0.2, aspect: 2.3 },
  { height: 0.24, z: 0.36, aspect: 2.4 },
] as const;

const backdrop: CutoutSpec = {
  key: "craft:layer0",
  aspect: LAYERS[0].aspect,
  base: PALETTE.sky,
  seed: 71,
  resolution: 640,
  paint(k) {
    const w = k.aspect;
    const m = 0.03;
    const card = new Path2D();
    card.moveTo(m, -0.05);
    card.lineTo(m, 0.62);
    card.bezierCurveTo(m, 0.88, w * 0.22, 0.975, w / 2, 0.975);
    card.bezierCurveTo(w * 0.78, 0.975, w - m, 0.88, w - m, 0.62);
    card.lineTo(w - m, -0.05);
    card.closePath();
    k.shape(card, PALETTE.sky);
    k.layer(rolling(m, w - m, 0.34, [[w * 0.28, 0.37], [w * 0.62, 0.33], [w * 0.88, 0.37]]), PALETTE.bandPale, 0.35);
    k.layer(rolling(m, w - m, 0.27, [[w * 0.2, 0.3], [w * 0.58, 0.27], [w * 0.84, 0.3]]), PALETTE.bandWarm, 0.4);
    k.layer(circle(w * 0.74, 0.66, 0.08), PALETTE.sun, 0.6);
    // The guide lines it was cut along, left on the card.
    pencil(k, [[m + 0.06, 0.5], [w - m - 0.06, 0.5]], 0.006, "rgba(70,58,48,0.22)");
    pencil(k, [[w * 0.12, 0.78], [w * 0.3, 0.86], [w * 0.46, 0.8]], 0.008);
  },
};

const hillsLayer: CutoutSpec = {
  key: "craft:layer1",
  aspect: LAYERS[1].aspect,
  base: PALETTE.hillFar,
  seed: 83,
  resolution: 640,
  paint(k) {
    const w = k.aspect;
    k.shape(rolling(0.02, w - 0.02, 0.5, [[w * 0.18, 0.86], [w * 0.46, 0.62], [w * 0.74, 0.95], [w * 0.92, 0.7]]), PALETTE.hillFar);
    k.layer(rolling(0.02, w - 0.02, 0.32, [[w * 0.3, 0.52], [w * 0.62, 0.36], [w * 0.86, 0.5]]), PALETTE.hillNear, 0.6);
    pencil(k, [[w * 0.5, 0.2], [w * 0.62, 0.24], [w * 0.74, 0.2]], 0.01);
  },
};

const groundLayer: CutoutSpec = {
  key: "craft:layer2",
  aspect: LAYERS[2].aspect,
  base: PALETTE.lawnDeep,
  seed: 97,
  resolution: 640,
  paint(k) {
    const w = k.aspect;
    const tufts = new Path2D();
    tufts.moveTo(0.02, -0.05);
    tufts.lineTo(0.02, 0.45);
    for (let i = 0; i <= 12; i++) {
      const x = 0.02 + ((w - 0.04) * i) / 12;
      tufts.quadraticCurveTo(x - 0.05, 0.62 + 0.3 * k.rand(), x, 0.42 + 0.18 * k.rand());
    }
    tufts.lineTo(w - 0.02, -0.05);
    tufts.closePath();
    k.shape(tufts, PALETTE.lawnDeep);
    k.layer(rolling(0.02, w - 0.02, 0.22, [[w * 0.25, 0.32], [w * 0.7, 0.26]]), PALETTE.lawn, 0.5);
  },
};

const LAYER_SPECS = [backdrop, hillsLayer, groundLayer];

export function layerCutout(i: number): Cutout {
  return getCutout(LAYER_SPECS[Math.max(0, Math.min(LAYER_SPECS.length - 1, i))]);
}

// ---------------------------------------------------------------------------
// Props: a stand-up card per prop, its silhouette by the prop's size (the
// studio says how big a thing is, not what it looks like), cut from the
// prop's paper.

/** Height (stage units) and aspect of a prop's card by its size. */
export const PROP_SHAPES: Record<PropChip["size"], { height: number; aspect: number }> = {
  tiny: { height: 0.26, aspect: 0.9 },
  hand: { height: 0.34, aspect: 1.05 },
  carried: { height: 0.42, aspect: 1 },
  furniture: { height: 0.5, aspect: 1.25 },
  vehicle: { height: 0.42, aspect: 1.9 },
  building: { height: 0.7, aspect: 0.95 },
};

function shade(hex: string, k: number): string {
  const n = parseInt(hex.slice(1), 16);
  const ch = (s: number) => Math.round(((n >> s) & 255) * (1 - k));
  return `rgb(${ch(16)},${ch(8)},${ch(0)})`;
}

function tint(hex: string, k: number): string {
  const n = parseInt(hex.slice(1), 16);
  const ch = (s: number) => Math.round(((n >> s) & 255) + (255 - ((n >> s) & 255)) * k);
  return `rgb(${ch(16)},${ch(8)},${ch(0)})`;
}

function propSpec(size: PropChip["size"], color: string): CutoutSpec {
  const { aspect } = PROP_SHAPES[size] ?? PROP_SHAPES.hand;
  return {
    key: `craft:prop:${size}:${color}`,
    aspect,
    base: color,
    seed: 101 + aspect * 10,
    resolution: 256,
    paint(k) {
      const w = k.aspect;
      const dark = shade(color, 0.22);
      const light = tint(color, 0.35);
      switch (size) {
        case "vehicle": {
          k.shape(roundRect(0.04, 0.14, w - 0.04, 0.58, 0.1), color);
          k.shape(poly([[w * 0.26, 0.56], [w * 0.36, 0.9], [w * 0.7, 0.9], [w * 0.82, 0.56]]), color);
          k.layer(poly([[w * 0.33, 0.6], [w * 0.4, 0.82], [w * 0.52, 0.82], [w * 0.52, 0.6]]), light, 0.4);
          k.layer(poly([[w * 0.56, 0.6], [w * 0.56, 0.82], [w * 0.67, 0.82], [w * 0.75, 0.6]]), light, 0.4);
          k.shape(circle(w * 0.25, 0.15, 0.15), dark);
          k.shape(circle(w * 0.75, 0.15, 0.15), dark);
          k.shape(rect(w * 0.18, -0.05, w * 0.82, 0.08), dark);
          break;
        }
        case "building": {
          k.shape(rect(w * 0.12, -0.05, w * 0.88, 0.62), color);
          k.shape(poly([[w * 0.04, 0.6], [w / 2, 0.97], [w * 0.96, 0.6]]), dark);
          k.layer(roundRect(w * 0.42, 0.0, w * 0.58, 0.3, 0.05), dark, 0.6);
          k.hole(rect(w * 0.22, 0.38, w * 0.34, 0.5));
          k.hole(rect(w * 0.66, 0.38, w * 0.78, 0.5));
          break;
        }
        case "furniture": {
          k.shape(roundRect(0.02, 0.62, w - 0.02, 0.8, 0.04), color);
          k.shape(rect(0.1, -0.05, 0.22, 0.66), dark);
          k.shape(rect(w - 0.22, -0.05, w - 0.1, 0.66), dark);
          k.layer(roundRect(0.08, 0.66, w - 0.08, 0.76, 0.03), light, 0.3);
          break;
        }
        case "carried": {
          const handle = new Path2D();
          handle.arc(w / 2, 0.78, 0.17, Math.PI, 0);
          handle.arc(w / 2, 0.78, 0.09, 0, Math.PI, true);
          handle.closePath();
          k.shape(handle, dark);
          k.shape(roundRect(0.06, -0.05, w - 0.06, 0.8, 0.08), color);
          k.layer(rect(0.06, 0.34, w - 0.06, 0.44), light, 0.4);
          break;
        }
        default: {
          // A small held thing: a little book-like card with a spine and a band.
          k.shape(roundRect(0.08, -0.05, w - 0.08, 0.9, 0.06), color);
          k.layer(rect(0.08, -0.02, 0.2, 0.9), dark, 0.4);
          k.layer(rect(0.3, 0.55, w - 0.16, 0.62), light, 0.3);
          break;
        }
      }
    },
  };
}

export function propCutout(size: PropChip["size"], color: string): Cutout {
  return getCutout(propSpec(PROP_SHAPES[size] ? size : "hand", color));
}

// ---------------------------------------------------------------------------
// The waiting note: a small folded card with a pencilled question mark.

const waitSpec: CutoutSpec = {
  key: "craft:wait",
  aspect: 0.72,
  base: "#f7f0e2",
  seed: 131,
  resolution: 256,
  paint(k) {
    const w = k.aspect;
    k.shape(roundRect(0.04, -0.05, w - 0.04, 0.95, 0.06), "#f7f0e2");
    // Piece units run y up: a hook over the top, down to the stem.
    const c = w / 2;
    const q = new Path2D();
    q.moveTo(c - 0.15, 0.64);
    q.bezierCurveTo(c - 0.15, 0.84, c + 0.15, 0.84, c + 0.15, 0.66);
    q.bezierCurveTo(c + 0.15, 0.54, c, 0.52, c, 0.42);
    q.lineTo(c, 0.35);
    k.line(q, ACCENT, 0.065);
    k.layer(circle(c, 0.2, 0.045), ACCENT, 0.2);
  },
};

export function waitCutout(): Cutout {
  return getCutout(waitSpec);
}

// ---------------------------------------------------------------------------
// Flat prints: a storyboard frame, a name slip, a tick.

const prints = new Map<string, Texture>();

function printTexture(key: string, w: number, h: number, draw: (ctx: CanvasRenderingContext2D) => void): Texture {
  const hit = prints.get(key);
  if (hit) return hit;
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("2D canvas unavailable");
  draw(ctx);
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  t.anisotropy = 4;
  prints.set(key, t);
  return t;
}

/** Aspect (width / depth) of a storyboard frame card. */
export const FRAME_ASPECT = 1.5;

/** Storyboard frame `i`: a card with a pencilled box and a quick sketch of a shot. */
export function frameTexture(i: number, fonts: PageFonts): Texture {
  return printTexture(`frame:${i}`, 360, 240, (ctx) => {
    const W = 360;
    const H = 240;
    const r = rng(7 + i * 13);
    paper(ctx, W, H, { seed: 40 + i, tone: "#fbf7ee" });
    ctx.strokeStyle = "rgba(70,58,48,0.55)";
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const x0 = 22;
    const y0 = 22;
    const x1 = W - 22;
    const y1 = H - 46;
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
    // A horizon, a hill, and one or two figures: a shot, in a few strokes.
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    const hy = y0 + (y1 - y0) * (0.55 + r() * 0.15);
    ctx.moveTo(x0 + 6, hy);
    ctx.bezierCurveTo(x0 + 80, hy - 18 - r() * 12, x1 - 120, hy + 6, x1 - 6, hy - 10);
    ctx.stroke();
    const figures = 1 + (i % 2);
    for (let f = 0; f < figures; f++) {
      const fx = x0 + (x1 - x0) * (0.32 + f * 0.3 + (r() - 0.5) * 0.08);
      const fy = hy + 6;
      const s = 0.85 + r() * 0.3;
      ctx.beginPath();
      ctx.arc(fx, fy - 62 * s, 9 * s, 0, Math.PI * 2);
      ctx.moveTo(fx, fy - 53 * s);
      ctx.lineTo(fx, fy - 22 * s);
      ctx.lineTo(fx - 9 * s, fy);
      ctx.moveTo(fx, fy - 22 * s);
      ctx.lineTo(fx + 9 * s, fy);
      ctx.moveTo(fx - 14 * s, fy - 42 * s);
      ctx.lineTo(fx + 14 * s, fy - 38 * s + (f ? -10 : 6));
      ctx.stroke();
    }
    // An arrow for the camera's move.
    ctx.beginPath();
    ctx.moveTo(x1 - 70, y0 + 26);
    ctx.lineTo(x1 - 26, y0 + 26);
    ctx.moveTo(x1 - 36, y0 + 18);
    ctx.lineTo(x1 - 26, y0 + 26);
    ctx.lineTo(x1 - 36, y0 + 34);
    ctx.stroke();
    ctx.fillStyle = INK_SOFT;
    ctx.font = `500 30px ${fonts.hand}`;
    ctx.textBaseline = "alphabetic";
    ctx.fillText(`shot ${i + 1}`, x0 + 2, H - 14);
  });
}

/** Aspect (width / depth) of a name slip. */
export const SLIP_ASPECT = 3.2;

/** A torn paper slip with a prop's name in hand. */
export function slipTexture(name: string, fonts: PageFonts): Texture {
  return printTexture(`slip:${name}`, 384, 120, (ctx) => {
    const W = 384;
    const H = 120;
    const r = rng(name.length * 31 + 5);
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(6, 10);
    for (let x = 6; x <= W - 6; x += 24) ctx.lineTo(x, 8 + r() * 6);
    ctx.lineTo(W - 6, H - 10);
    for (let x = W - 6; x >= 6; x -= 24) ctx.lineTo(x, H - 8 - r() * 6);
    ctx.closePath();
    ctx.clip();
    paper(ctx, W, H, { seed: 60 + name.length, tone: "#fbf6ea" });
    ctx.restore();
    ctx.fillStyle = INK;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    let size = 58;
    ctx.font = `600 ${size}px ${fonts.hand}`;
    while (size > 30 && ctx.measureText(name).width > W - 40) {
      size -= 4;
      ctx.font = `600 ${size}px ${fonts.hand}`;
    }
    ctx.fillText(name, W / 2, H / 2 + 2, W - 30);
  });
}

/** A red pencil tick, for a frame the checker looked at. */
export function tickTexture(): Texture {
  return printTexture("tick", 128, 128, (ctx) => {
    ctx.strokeStyle = ACCENT;
    ctx.lineWidth = 12;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(22, 70);
    ctx.quadraticCurveTo(40, 82, 52, 104);
    ctx.quadraticCurveTo(70, 50, 110, 20);
    ctx.stroke();
  });
}

/** Build the set's cut-outs ahead, one per idle slice, so the first layer rises without a hitch. */
export function warmCraftPieces(): () => void {
  const w = window as Window & {
    requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number;
    cancelIdleCallback?: (id: number) => void;
  };
  const jobs: (() => void)[] = [0, 1, 2].map((i) => () => void layerCutout(i));
  jobs.push(() => void waitCutout());
  let cancelled = false;
  let handle = 0;
  const next = () => {
    if (cancelled) return;
    const job = jobs.shift();
    if (!job) return;
    job();
    schedule();
  };
  const schedule = () => {
    if (w.requestIdleCallback) handle = w.requestIdleCallback(next, { timeout: 1500 });
    else handle = window.setTimeout(next, 60);
  };
  schedule();
  return () => {
    cancelled = true;
    if (w.cancelIdleCallback) w.cancelIdleCallback(handle);
    else window.clearTimeout(handle);
  };
}
