/**
 * Cut-paper art for a set's layers (CONTRACTS.md "Stage space": a layer spans
 * the full stage width, 2 units, and is `height` units tall when risen).
 *
 * - `art: null` (no cutout generated yet): a placeholder card in the layer's
 *   `color`, cut like a real pop-up layer and layered in tones of that colour,
 *   with a silhouette by depth so the three planes read apart: the back is a
 *   tall backdrop with soft shoulders, the middle a terraced band, the front a
 *   scalloped hedge-like edge. Deliberately abstract: it says "this is where
 *   the art goes" without inventing content.
 * - `art: "media/<id>"`: the image becomes the cutout (its colour is the
 *   colour pass and its alpha the mask), so generated PNGs get the same
 *   deckled edge and card walls. Loaded once per URL.
 *
 * Risers: a mark with a standing surface above the page (h > 0) gets a
 * paper platform under it, so nobody stands on air (see riserArt).
 */

import type { SetLayer } from "@/lib/types";
import type { CutoutSpec, PaintKit } from "../cutout";
import { rng } from "../timeline";

/**
 * The family's own media comes from the API on another port, behind the session
 * cookie, so it is fetched with credentials (the API answers with the exact origin
 * and Allow-Credentials). Anything else stays anonymous, which a CDN without
 * credentialed CORS still serves.
 */
function artCrossOrigin(url: string): "use-credentials" | "anonymous" {
  return /\/families\/[^/]+\/media\//.test(url) ? "use-credentials" : "anonymous";
}

/** Width of every layer in stage units. */
export const LAYER_WIDTH = 2;

const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);

/** Hex colour mixed towards white (k > 0) or a warm shadow (k < 0). */
export function tone(hex: string, k: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  const n = m ? parseInt(m[1], 16) : 0xc4a37a;
  let r = (n >> 16) & 255;
  let g = (n >> 8) & 255;
  let b = n & 255;
  if (k >= 0) {
    r += (252 - r) * k;
    g += (246 - g) * k;
    b += (234 - b) * k;
  } else {
    const s = -k;
    r += (58 - r) * s;
    g += (36 - g) * s;
    b += (22 - b) * s;
  }
  const h = (v: number) => Math.round(clamp01(v / 255) * 255).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** Long-side texture size: wide, short layers need more pixels across. */
function resolutionFor(aspect: number): number {
  return Math.min(1536, Math.round(512 * Math.sqrt(Math.max(1, aspect))));
}

type Pt = [number, number];

function closed(pts: Pt[]): Path2D {
  const p = new Path2D();
  p.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) p.lineTo(pts[i][0], pts[i][1]);
  p.closePath();
  return p;
}

/** Back: a tall backdrop with rounded shoulders and an inset panel. */
function paintBack(k: PaintKit, color: string) {
  const w = k.aspect;
  const m = 0.012;
  const r = Math.min(0.22, w * 0.12);
  const card = new Path2D();
  card.moveTo(m, -0.05);
  card.lineTo(m, 0.985 - r);
  card.quadraticCurveTo(m, 0.985, m + r, 0.985);
  card.lineTo(w - m - r, 0.985);
  card.quadraticCurveTo(w - m, 0.985, w - m, 0.985 - r);
  card.lineTo(w - m, -0.05);
  card.closePath();
  k.shape(card, color);
  // A paler panel glued on, then a deeper band along the foot: a stage flat.
  const inset = 0.07;
  const pr = r * 0.7;
  const panel = new Path2D();
  panel.moveTo(m + inset, 0.16);
  panel.lineTo(m + inset, 0.985 - inset - pr);
  panel.quadraticCurveTo(m + inset, 0.985 - inset, m + inset + pr, 0.985 - inset);
  panel.lineTo(w - m - inset - pr, 0.985 - inset);
  panel.quadraticCurveTo(w - m - inset, 0.985 - inset, w - m - inset, 0.985 - inset - pr);
  panel.lineTo(w - m - inset, 0.16);
  panel.closePath();
  k.layer(panel, tone(color, 0.1), 0.45);
  // A few torn scraps of paler and deeper card glued over the panel: the
  // collage a painted backdrop starts as. Kept low and soft, so it reads as
  // texture behind the cast, never as a picture of something.
  const scraps: [number, number, number, number, number][] = [
    [0.22, 0.72, 0.2, 0.11, 0.12],
    [0.7, 0.58, 0.26, 0.13, -0.04],
    [0.42, 0.34, 0.18, 0.09, 0.09],
    [0.86, 0.3, 0.14, 0.1, -0.05],
  ];
  for (const [u, v, rx, ry, t] of scraps) k.layer(tornBlob(k, u * w, v, rx * w * 0.5, ry, 18), tone(color, t), 0.22);
  k.layer(closed([[m, -0.05], [m, 0.12], [w - m, 0.12], [w - m, -0.05]]), tone(color, -0.1), 0.5);
}

/** A torn scrap: an ellipse with a ragged rim. */
function tornBlob(k: PaintKit, cx: number, cy: number, rx: number, ry: number, n: number): Path2D {
  const pts: Pt[] = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const j = 0.82 + k.rand() * 0.3;
    pts.push([cx + Math.cos(a) * rx * j, cy + Math.sin(a) * ry * j]);
  }
  return closed(pts);
}

/** Middle: a band with a gently terraced top. */
function paintMiddle(k: PaintKit, color: string) {
  const w = k.aspect;
  const m = 0.012;
  const steps = 5;
  const pts: Pt[] = [[m, -0.05]];
  for (let i = 0; i < steps; i++) {
    const x0 = m + ((w - 2 * m) * i) / steps;
    const x1 = m + ((w - 2 * m) * (i + 1)) / steps;
    const centre = 1 - Math.abs((i + 0.5) / steps - 0.5) * 2; // 0 edges .. 1 middle
    const y = 0.8 + 0.18 * centre + (k.rand() - 0.5) * 0.03;
    pts.push([x0, y], [x1, y]);
  }
  pts.push([w - m, -0.05]);
  k.shape(closed(pts), color);
  // Tonal strata: a paler top course and a shaded foot.
  const top: Pt[] = [[m, 0.55]];
  for (let i = 1; i < pts.length - 1; i++) top.push([pts[i][0], pts[i][1] - 0.001]);
  top.push([w - m, 0.55]);
  k.layer(closed(top), tone(color, 0.16), 0.4);
  k.layer(closed([[m, -0.05], [m, 0.22], [w - m, 0.2], [w - m, -0.05]]), tone(color, -0.09), 0.45);
}

/** Front: a low scalloped edge, the foreground hedge of a pop-up page. */
function paintFront(k: PaintKit, color: string) {
  const w = k.aspect;
  const m = 0.01;
  const bumps = Math.max(6, Math.round(w * 1.6));
  const scallop = (lift: number, amp: number, phase: number) => {
    const p = new Path2D();
    p.moveTo(m, -0.05);
    p.lineTo(m, lift);
    const step = (w - 2 * m) / bumps;
    for (let i = 0; i < bumps; i++) {
      const x0 = m + i * step;
      const h = amp * (0.75 + 0.5 * k.rand());
      p.bezierCurveTo(x0 + step * 0.05, lift + h * (1 + phase), x0 + step * 0.95, lift + h * (1 + phase), x0 + step, lift);
    }
    p.lineTo(w - m, -0.05);
    p.closePath();
    return p;
  };
  k.shape(scallop(0.62, 0.36, 0), color);
  k.layer(scallop(0.42, 0.3, 0.05), tone(color, -0.12), 0.55);
  k.layer(closed([[m, -0.05], [m, 0.14], [w - m, 0.14], [w - m, -0.05]]), tone(color, -0.2), 0.4);
}

/** The placeholder card for a layer with no art. */
export function placeholderLayerSpec(setId: string, index: number, layer: SetLayer): CutoutSpec {
  const color = layer.color ?? "#d9c7a6";
  const height = Math.max(0.05, layer.height);
  const aspect = LAYER_WIDTH / height;
  const depth = Math.round(layer.depth);
  const paint = depth <= 0 ? paintBack : depth === 1 ? paintMiddle : paintFront;
  return {
    key: `set:${setId}.layer${index}:placeholder:${color}:${height}:${depth}`,
    aspect,
    base: color,
    seed: 17 + index * 31 + depth * 7,
    resolution: resolutionFor(aspect),
    deckle: 0.9,
    paint: (k) => paint(k, color),
  };
}

/**
 * A cutout from a loaded image: its colour as the colour pass, its alpha
 * (white where opaque) as the mask. The image is stretched to the layer's
 * full width and height, as the contract places it.
 */
export function imageLayerSpec(key: string, image: CanvasImageSource, height: number, base = "#e9dfcb"): CutoutSpec {
  const aspect = LAYER_WIDTH / Math.max(0.05, height);
  let silhouette: HTMLCanvasElement | null = null;
  return {
    key,
    aspect,
    base,
    resolution: resolutionFor(aspect),
    deckle: 0.6,
    paint(k) {
      const { ctx } = k;
      const W = ctx.canvas.width;
      const H = ctx.canvas.height;
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (k.pass === "color") {
        ctx.drawImage(image, 0, 0, W, H);
      } else {
        if (!silhouette || silhouette.width !== W || silhouette.height !== H) {
          silhouette = document.createElement("canvas");
          silhouette.width = W;
          silhouette.height = H;
          const s = silhouette.getContext("2d");
          if (s) {
            s.drawImage(image, 0, 0, W, H);
            s.globalCompositeOperation = "source-in";
            s.fillStyle = "#fff";
            s.fillRect(0, 0, W, H);
          }
        }
        ctx.drawImage(silhouette, 0, 0);
      }
      ctx.restore();
    },
  };
}

const images = new Map<string, Promise<HTMLImageElement>>();

/** Loads an image once per URL (CORS-enabled, since it is read back from a canvas). */
export function loadImage(url: string): Promise<HTMLImageElement> {
  let p = images.get(url);
  if (!p) {
    p = new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = artCrossOrigin(url);
      img.decoding = "async";
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error(`could not load ${url}`));
      img.src = url;
    });
    images.set(url, p);
    p.catch(() => images.delete(url));
  }
  return p;
}

/**
 * The front face of a riser: a few stone treads in the colour of the layer
 * behind it, so the platform reads as part of the set (steps, a stage, a
 * kerb) rather than a box.
 */
export function riserArt(key: string, aspect: number, color: string): CutoutSpec {
  return {
    key: `riser:${key}:${color}:${aspect.toFixed(3)}`,
    aspect,
    base: color,
    seed: 5,
    resolution: 384,
    deckle: 0.4,
    paint(k) {
      const w = k.aspect;
      k.shape(closed([[0, -0.05], [0, 1.05], [w, 1.05], [w, -0.05]]), color);
      const treads = 3;
      const rand = rng(11);
      for (let i = 0; i < treads; i++) {
        const y0 = i / treads;
        const y1 = (i + 1) / treads;
        // Each tread: a paler nosing on top, a shaded riser face below it.
        k.layer(closed([[0, y1 - 0.09], [0, y1], [w, y1], [w, y1 - 0.09]]), tone(color, 0.18), 0.35);
        k.layer(closed([[0, y0], [0, y0 + 0.07], [w, y0 + 0.07], [w, y0]]), tone(color, -0.1), 0.3);
        const joint = new Path2D();
        const x = w * (0.25 + 0.5 * rand());
        joint.moveTo(x, y0 + 0.08);
        joint.lineTo(x + (rand() - 0.5) * 0.01, y1 - 0.1);
        k.line(joint, tone(color, -0.22), 0.006);
      }
    },
  };
}
