/**
 * Everything printed in the book, drawn in code on <canvas>: paper, the story
 * page, the empty stage page, the title page, the card cover with its stitched
 * cloth spine, endpapers, the page-block edges and painted stand-in photos.
 * Browser-only (call from effects). No image assets.
 */

import type { BookCover, BookPhoto, BookSpread, PlaceholderScene } from "./types";

/* ------------------------------------------------------------------------ */
/* Basics                                                                    */
/* ------------------------------------------------------------------------ */

export function rng(seed: number): () => number {
  let a = (seed * 2654435761) >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function canvas(w: number, h: number) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("2D canvas unavailable");
  return { c, ctx };
}

export const INK = "#2f2620";
export const INK_SOFT = "#5c4e43";
export const INK_FAINT = "#8f8173";
export const ACCENT = "#b9573d";
export const PAPER = "#f4ecdc";

export interface PageFonts {
  display: string;
  hand: string;
  sans: string;
}

function cssVar(name: string, fallback: string) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v ? `${v}, ${fallback}` : fallback;
}

/** Font stacks from next/font's CSS variables, loaded and ready for canvas. */
export async function loadPageFonts(): Promise<PageFonts> {
  const fonts: PageFonts = {
    display: cssVar("--font-fraunces", "Georgia, serif"),
    hand: cssVar("--font-caveat", "cursive"),
    sans: cssVar("--font-nunito", "system-ui, sans-serif"),
  };
  const loads = [
    `600 64px ${fonts.display}`,
    `400 30px ${fonts.display}`,
    `italic 400 30px ${fonts.display}`,
    `500 40px ${fonts.hand}`,
    `700 20px ${fonts.sans}`,
  ];
  try {
    await Promise.all(loads.map((f) => document.fonts.load(f)));
  } catch {
    // Fall back to the stack's system fonts.
  }
  return fonts;
}

type Ctx = CanvasRenderingContext2D;

function setSpacing(ctx: Ctx, px: number) {
  // Canvas letterSpacing is Chromium/Safari/Firefox 2023+; harmless if absent.
  (ctx as Ctx & { letterSpacing?: string }).letterSpacing = `${px}px`;
}

function wrap(ctx: Ctx, text: string, maxW: number): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let line = "";
  for (const w of words) {
    const t = line ? `${line} ${w}` : w;
    if (ctx.measureText(t).width > maxW && line) {
      lines.push(line);
      line = w;
    } else line = t;
  }
  if (line) lines.push(line);
  return lines;
}

/* ------------------------------------------------------------------------ */
/* Paper                                                                     */
/* ------------------------------------------------------------------------ */

interface PaperOptions {
  seed: number;
  /** Which side the spine is on, for the gutter shade. */
  gutter?: "left" | "right";
  tone?: string;
}

function paper(ctx: Ctx, w: number, h: number, o: PaperOptions) {
  const r = rng(o.seed);
  ctx.fillStyle = o.tone ?? PAPER;
  ctx.fillRect(0, 0, w, h);

  // Soft mottling: the sheet is never one flat colour.
  for (let i = 0; i < 14; i++) {
    const x = r() * w;
    const y = r() * h;
    const rad = (0.15 + r() * 0.35) * Math.max(w, h);
    const g = ctx.createRadialGradient(x, y, 0, x, y, rad);
    const light = r() > 0.5;
    g.addColorStop(0, light ? "rgba(255,252,244,0.22)" : "rgba(196,163,122,0.07)");
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  }

  // Fibres and specks.
  ctx.lineCap = "round";
  for (let i = 0; i < (w * h) / 2600; i++) {
    const x = r() * w;
    const y = r() * h;
    const a = r() * Math.PI;
    const l = 3 + r() * 11;
    ctx.strokeStyle = r() > 0.5 ? "rgba(140,108,72,0.07)" : "rgba(255,255,250,0.35)";
    ctx.lineWidth = 0.6 + r() * 0.8;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.quadraticCurveTo(x + Math.cos(a) * l * 0.5 + (r() - 0.5) * 3, y + Math.sin(a) * l * 0.5, x + Math.cos(a) * l, y + Math.sin(a) * l);
    ctx.stroke();
  }
  for (let i = 0; i < (w * h) / 22000; i++) {
    ctx.fillStyle = `rgba(110,80,50,${0.08 + r() * 0.18})`;
    ctx.beginPath();
    ctx.arc(r() * w, r() * h, 0.6 + r() * 1.3, 0, Math.PI * 2);
    ctx.fill();
  }

  // Edges age a little warmer.
  const edge = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.45, w / 2, h / 2, Math.max(w, h) * 0.78);
  edge.addColorStop(0, "rgba(0,0,0,0)");
  edge.addColorStop(1, "rgba(150,110,60,0.13)");
  ctx.fillStyle = edge;
  ctx.fillRect(0, 0, w, h);

  if (o.gutter) {
    const gw = w * 0.14;
    const x0 = o.gutter === "left" ? 0 : w;
    const g = ctx.createLinearGradient(x0, 0, o.gutter === "left" ? gw : w - gw, 0);
    g.addColorStop(0, "rgba(92,60,30,0.2)");
    g.addColorStop(0.35, "rgba(92,60,30,0.06)");
    g.addColorStop(1, "rgba(92,60,30,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  }
}

/* ------------------------------------------------------------------------ */
/* Tape, thread, photos                                                      */
/* ------------------------------------------------------------------------ */

const TAPES = ["rgba(233,220,189,0.82)", "rgba(217,166,160,0.72)", "rgba(169,192,211,0.72)", "rgba(196,213,180,0.74)"];

/** A strip of masking / washi tape with torn ends, centred on (cx, cy). */
export function drawTape(ctx: Ctx, cx: number, cy: number, w: number, h: number, angle: number, seed: number, color?: string) {
  const r = rng(seed + 17);
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(angle);
  ctx.beginPath();
  const teeth = 6;
  ctx.moveTo(-w / 2, -h / 2);
  ctx.lineTo(w / 2, -h / 2);
  for (let i = 1; i <= teeth; i++) ctx.lineTo(w / 2 - r() * h * 0.14, -h / 2 + (h * i) / teeth);
  ctx.lineTo(-w / 2, h / 2);
  for (let i = teeth - 1; i >= 0; i--) ctx.lineTo(-w / 2 + r() * h * 0.14, -h / 2 + (h * i) / teeth);
  ctx.closePath();
  ctx.shadowColor = "rgba(74,47,29,0.18)";
  ctx.shadowBlur = 3;
  ctx.shadowOffsetY = 1;
  ctx.fillStyle = color ?? TAPES[Math.floor(r() * TAPES.length)];
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.clip();
  // Crinkle and sheen.
  const g = ctx.createLinearGradient(0, -h / 2, 0, h / 2);
  g.addColorStop(0, "rgba(255,255,255,0.3)");
  g.addColorStop(0.4, "rgba(255,255,255,0)");
  g.addColorStop(1, "rgba(0,0,0,0.05)");
  ctx.fillStyle = g;
  ctx.fillRect(-w / 2, -h / 2, w, h);
  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  for (let x = -w / 2; x < w / 2; x += 5 + r() * 6) {
    ctx.lineWidth = 1 + r();
    ctx.beginPath();
    ctx.moveTo(x, -h / 2);
    ctx.lineTo(x + (r() - 0.5) * 4, h / 2);
    ctx.stroke();
  }
  ctx.restore();
}

/** A dashed running stitch of thread from (x0, y0) to (x1, y1). */
function drawStitchLine(ctx: Ctx, x0: number, y0: number, x1: number, y1: number, color: string, width = 3, dash = 15, gap = 10) {
  const len = Math.hypot(x1 - x0, y1 - y0);
  const ux = (x1 - x0) / len;
  const uy = (y1 - y0) / len;
  ctx.save();
  ctx.lineCap = "round";
  for (let d = 0; d < len - dash * 0.5; d += dash + gap) {
    const e = Math.min(len, d + dash);
    // Holes where the needle went through.
    ctx.fillStyle = "rgba(60,40,25,0.22)";
    ctx.beginPath();
    ctx.arc(x0 + ux * (d - 1.5), y0 + uy * (d - 1.5), width * 0.55, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(60,40,25,0.25)";
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(x0 + ux * d + 0.8, y0 + uy * d + 1.2);
    ctx.lineTo(x0 + ux * e + 0.8, y0 + uy * e + 1.2);
    ctx.stroke();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(x0 + ux * d, y0 + uy * d);
    ctx.lineTo(x0 + ux * e, y0 + uy * e);
    ctx.stroke();
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.lineWidth = width * 0.35;
    ctx.beginPath();
    ctx.moveTo(x0 + ux * d - uy, y0 + uy * d - ux * 0.6);
    ctx.lineTo(x0 + ux * e - uy, y0 + uy * e - ux * 0.6);
    ctx.stroke();
  }
  ctx.restore();
}

let grainTile: HTMLCanvasElement | null = null;
function photoGrain(): HTMLCanvasElement {
  if (grainTile) return grainTile;
  const { c, ctx } = canvas(128, 128);
  const img = ctx.createImageData(128, 128);
  const r = rng(5);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = 128 + (r() - 0.5) * 90;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  grainTile = c;
  return c;
}

function sky(ctx: Ctx, w: number, h: number, top: string, bottom: string, until = 1) {
  const g = ctx.createLinearGradient(0, 0, 0, h * until);
  g.addColorStop(0, top);
  g.addColorStop(1, bottom);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
}

function figure(ctx: Ctx, x: number, base: number, s: number, body: string, dress = false) {
  ctx.fillStyle = body;
  ctx.beginPath();
  if (dress) {
    ctx.moveTo(x - s * 0.42, base);
    ctx.lineTo(x + s * 0.42, base);
    ctx.lineTo(x + s * 0.14, base - s * 1.25);
    ctx.lineTo(x - s * 0.14, base - s * 1.25);
  } else {
    ctx.roundRect(x - s * 0.2, base - s * 1.3, s * 0.4, s * 1.3, s * 0.12);
  }
  ctx.fill();
  ctx.fillStyle = "#3b2a20";
  ctx.beginPath();
  ctx.arc(x, base - s * 1.45, s * 0.2, 0, Math.PI * 2);
  ctx.fill();
}

/** A painted stand-in photo, in faded print colours. */
export function paintScene(scene: PlaceholderScene, w: number, h: number, seed = 1): HTMLCanvasElement {
  const { c, ctx } = canvas(w, h);
  const r = rng(seed);
  switch (scene) {
    case "steps": {
      sky(ctx, w, h, "#a9b3b4", "#d8d3c4", 0.55);
      // Brick church front with an arched door.
      ctx.fillStyle = "#a35f45";
      ctx.fillRect(w * 0.08, h * 0.08, w * 0.84, h * 0.62);
      ctx.strokeStyle = "rgba(60,30,20,0.18)";
      ctx.lineWidth = 1;
      for (let y = h * 0.08; y < h * 0.7; y += h * 0.035) {
        ctx.beginPath();
        ctx.moveTo(w * 0.08, y);
        ctx.lineTo(w * 0.92, y);
        ctx.stroke();
      }
      ctx.fillStyle = "#e9dcc0";
      ctx.beginPath();
      ctx.moveTo(w * 0.36, h * 0.7);
      ctx.lineTo(w * 0.36, h * 0.36);
      ctx.arc(w * 0.5, h * 0.36, w * 0.14, Math.PI, 0);
      ctx.lineTo(w * 0.64, h * 0.7);
      ctx.fill();
      ctx.fillStyle = "#4a3326";
      ctx.beginPath();
      ctx.moveTo(w * 0.39, h * 0.7);
      ctx.lineTo(w * 0.39, h * 0.38);
      ctx.arc(w * 0.5, h * 0.38, w * 0.11, Math.PI, 0);
      ctx.lineTo(w * 0.61, h * 0.7);
      ctx.fill();
      for (const x of [0.17, 0.76]) {
        ctx.fillStyle = "#e9dcc0";
        ctx.beginPath();
        ctx.moveTo(w * x, h * 0.42);
        ctx.lineTo(w * x, h * 0.26);
        ctx.arc(w * (x + 0.035), h * 0.26, w * 0.035, Math.PI, 0);
        ctx.lineTo(w * (x + 0.07), h * 0.42);
        ctx.fill();
        ctx.fillStyle = "#6f8aa0";
        ctx.fillRect(w * (x + 0.012), h * 0.27, w * 0.046, h * 0.14);
      }
      // Steps.
      for (let i = 0; i < 5; i++) {
        const y = h * (0.7 + i * 0.06);
        ctx.fillStyle = i % 2 ? "#c9c0b0" : "#d8cfbe";
        ctx.fillRect(w * (0.04 - i * 0.02), y, w * (0.92 + i * 0.04), h * 0.06);
        ctx.fillStyle = "rgba(70,50,35,0.18)";
        ctx.fillRect(w * (0.04 - i * 0.02), y, w * (0.92 + i * 0.04), 2);
      }
      // Two people and the scattered hymn books.
      figure(ctx, w * 0.42, h * 0.82, h * 0.16, "#b2473a", true);
      figure(ctx, w * 0.58, h * 0.88, h * 0.17, "#34465c");
      const books = ["#5b3a6d", "#2f4f6f", "#7a2f2f", "#3f5f3f"];
      for (let i = 0; i < 6; i++) {
        ctx.save();
        ctx.translate(w * (0.32 + r() * 0.4), h * (0.85 + r() * 0.1));
        ctx.rotate((r() - 0.5) * 1.6);
        ctx.fillStyle = books[i % books.length];
        ctx.fillRect(-w * 0.025, -h * 0.012, w * 0.05, h * 0.024);
        ctx.restore();
      }
      // Rain.
      ctx.strokeStyle = "rgba(255,255,255,0.28)";
      ctx.lineWidth = 1;
      for (let i = 0; i < 90; i++) {
        const x = r() * w;
        const y = r() * h;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x - 4, y + 14);
        ctx.stroke();
      }
      break;
    }
    case "sea": {
      sky(ctx, w, h, "#e7c9a2", "#f1dfbf", 0.5);
      const sun = ctx.createRadialGradient(w * 0.68, h * 0.3, 0, w * 0.68, h * 0.3, w * 0.22);
      sun.addColorStop(0, "rgba(255,240,205,1)");
      sun.addColorStop(0.3, "rgba(255,235,200,0.75)");
      sun.addColorStop(1, "rgba(255,235,200,0)");
      ctx.fillStyle = sun;
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#6f989b";
      ctx.fillRect(0, h * 0.5, w, h * 0.2);
      ctx.strokeStyle = "rgba(255,245,220,0.55)";
      for (let i = 0; i < 26; i++) {
        const y = h * (0.52 + r() * 0.16);
        const x = r() * w;
        ctx.lineWidth = 1 + r();
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + 10 + r() * 30, y);
        ctx.stroke();
      }
      const sand = ctx.createLinearGradient(0, h * 0.7, 0, h);
      sand.addColorStop(0, "#d9c29a");
      sand.addColorStop(1, "#c7aa7c");
      ctx.fillStyle = sand;
      ctx.beginPath();
      ctx.moveTo(0, h * 0.7);
      ctx.bezierCurveTo(w * 0.3, h * 0.68, w * 0.6, h * 0.74, w, h * 0.69);
      ctx.lineTo(w, h);
      ctx.lineTo(0, h);
      ctx.fill();
      figure(ctx, w * 0.36, h * 0.86, h * 0.18, "#b2473a", true);
      figure(ctx, w * 0.45, h * 0.87, h * 0.2, "#34465c");
      break;
    }
    case "wedding": {
      sky(ctx, w, h, "#d6cdbd", "#e8dfcf");
      ctx.fillStyle = "#cbbca3";
      ctx.beginPath();
      ctx.moveTo(w * 0.15, h);
      ctx.lineTo(w * 0.15, h * 0.32);
      ctx.arc(w * 0.5, h * 0.32, w * 0.35, Math.PI, 0);
      ctx.lineTo(w * 0.85, h);
      ctx.fill();
      ctx.fillStyle = "#5a4033";
      ctx.beginPath();
      ctx.moveTo(w * 0.24, h);
      ctx.lineTo(w * 0.24, h * 0.36);
      ctx.arc(w * 0.5, h * 0.36, w * 0.26, Math.PI, 0);
      ctx.lineTo(w * 0.76, h);
      ctx.fill();
      ctx.fillStyle = "#d8cfbe";
      ctx.fillRect(0, h * 0.86, w, h * 0.14);
      figure(ctx, w * 0.42, h * 0.9, h * 0.32, "#f7f1e6", true);
      figure(ctx, w * 0.6, h * 0.9, h * 0.34, "#2e3440");
      // Bouquet and confetti.
      ctx.fillStyle = "#c46a6a";
      ctx.beginPath();
      ctx.arc(w * 0.47, h * 0.7, w * 0.035, 0, Math.PI * 2);
      ctx.fill();
      const cols = ["#e7b7a6", "#f3e2b8", "#b9cdb8", "#ffffff"];
      for (let i = 0; i < 70; i++) {
        ctx.fillStyle = cols[i % cols.length];
        ctx.save();
        ctx.translate(r() * w, r() * h * 0.8);
        ctx.rotate(r() * 3);
        ctx.fillRect(-3, -2, 6, 4);
        ctx.restore();
      }
      break;
    }
    case "house": {
      sky(ctx, w, h, "#bfd0d2", "#e8e0c9", 0.6);
      ctx.fillStyle = "#8c9b6b";
      ctx.fillRect(0, h * 0.66, w, h * 0.34);
      ctx.fillStyle = "#7a8a5a";
      ctx.beginPath();
      ctx.arc(w * 0.84, h * 0.46, w * 0.14, 0, Math.PI * 2);
      ctx.arc(w * 0.78, h * 0.36, w * 0.1, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#5e4535";
      ctx.fillRect(w * 0.82, h * 0.5, w * 0.03, h * 0.17);
      // House with a crooked porch and a round window.
      ctx.fillStyle = "#d9a37c";
      ctx.fillRect(w * 0.22, h * 0.4, w * 0.46, h * 0.3);
      ctx.fillStyle = "#7b4c3a";
      ctx.beginPath();
      ctx.moveTo(w * 0.18, h * 0.41);
      ctx.lineTo(w * 0.45, h * 0.16);
      ctx.lineTo(w * 0.72, h * 0.41);
      ctx.fill();
      ctx.fillStyle = "#f3e5bd";
      ctx.beginPath();
      ctx.arc(w * 0.45, h * 0.31, w * 0.035, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillRect(w * 0.27, h * 0.47, w * 0.08, h * 0.08);
      ctx.fillRect(w * 0.55, h * 0.47, w * 0.08, h * 0.08);
      ctx.fillStyle = "#4f6a73";
      ctx.fillRect(w * 0.41, h * 0.52, w * 0.08, h * 0.18);
      ctx.save();
      ctx.translate(w * 0.45, h * 0.6);
      ctx.rotate(-0.03);
      ctx.fillStyle = "#efe6d4";
      ctx.fillRect(-w * 0.2, -h * 0.07, w * 0.4, h * 0.02);
      ctx.fillRect(-w * 0.19, -h * 0.07, w * 0.012, h * 0.1);
      ctx.fillRect(w * 0.18, -h * 0.07, w * 0.012, h * 0.1);
      ctx.restore();
      // Moving boxes.
      ctx.fillStyle = "#c4a37a";
      ctx.fillRect(w * 0.12, h * 0.74, w * 0.1, h * 0.08);
      ctx.fillRect(w * 0.16, h * 0.68, w * 0.08, h * 0.06);
      ctx.fillStyle = "rgba(80,55,35,0.25)";
      ctx.fillRect(w * 0.12, h * 0.775, w * 0.1, 2);
      break;
    }
    default: {
      sky(ctx, w, h, "#e3cfa6", "#efdcb8", 0.6);
      const sun = ctx.createRadialGradient(w * 0.7, h * 0.28, 0, w * 0.7, h * 0.28, w * 0.18);
      sun.addColorStop(0, "rgba(255,243,210,1)");
      sun.addColorStop(1, "rgba(255,243,210,0)");
      ctx.fillStyle = sun;
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#8a9a64";
      ctx.beginPath();
      ctx.ellipse(w * 0.8, h * 0.95, w * 0.6, h * 0.42, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#6f8458";
      ctx.beginPath();
      ctx.ellipse(w * 0.15, h * 1.02, w * 0.6, h * 0.42, 0, 0, Math.PI * 2);
      ctx.fill();
      // The green sedan on the road home.
      ctx.fillStyle = "#c9b48a";
      ctx.beginPath();
      ctx.moveTo(0, h * 0.86);
      ctx.bezierCurveTo(w * 0.4, h * 0.8, w * 0.6, h * 0.84, w, h * 0.78);
      ctx.lineTo(w, h * 0.84);
      ctx.bezierCurveTo(w * 0.6, h * 0.9, w * 0.4, h * 0.86, 0, h * 0.93);
      ctx.fill();
      ctx.fillStyle = "#4f6f4a";
      ctx.beginPath();
      ctx.roundRect(w * 0.42, h * 0.72, w * 0.22, h * 0.07, 6);
      ctx.roundRect(w * 0.46, h * 0.67, w * 0.12, h * 0.07, 8);
      ctx.fill();
      ctx.fillStyle = "#2a2a28";
      ctx.beginPath();
      ctx.arc(w * 0.47, h * 0.795, w * 0.022, 0, Math.PI * 2);
      ctx.arc(w * 0.59, h * 0.795, w * 0.022, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  // Print finish: faded, warm, grainy, darker corners.
  ctx.globalCompositeOperation = "saturation";
  ctx.fillStyle = "rgba(128,128,128,0.22)";
  ctx.fillRect(0, 0, w, h);
  ctx.globalCompositeOperation = "soft-light";
  ctx.fillStyle = "rgba(255,214,160,0.45)";
  ctx.fillRect(0, 0, w, h);
  ctx.globalCompositeOperation = "overlay";
  ctx.globalAlpha = 0.16;
  ctx.fillStyle = ctx.createPattern(photoGrain(), "repeat") ?? "#888";
  ctx.fillRect(0, 0, w, h);
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
  const v = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.3, w / 2, h / 2, Math.max(w, h) * 0.75);
  v.addColorStop(0, "rgba(40,25,10,0)");
  v.addColorStop(1, "rgba(40,25,10,0.32)");
  ctx.fillStyle = v;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "rgba(255,250,235,0.08)";
  ctx.fillRect(0, 0, w, h);
  return c;
}

function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

/** A print with a white border taped onto the page; returns its bottom y. */
async function drawPhoto(
  ctx: Ctx,
  photo: BookPhoto,
  x: number,
  y: number,
  width: number,
  angle: number,
  seed: number,
  fonts: PageFonts,
  tape: "top" | "corners",
) {
  const aspect = photo.aspect ?? 4 / 3;
  const pad = Math.round(width * 0.05);
  const imgW = width - pad * 2;
  const imgH = Math.round(imgW / aspect);
  const capH = photo.caption ? Math.round(width * 0.17) : 0;
  const h = imgH + pad * 2 + capH;

  let image: CanvasImageSource = paintScene(photo.scene ?? "hills", imgW * 2, imgH * 2, seed);
  if (photo.src) {
    const loaded = await loadImage(photo.src);
    if (loaded) image = loaded;
  }

  ctx.save();
  ctx.translate(x + width / 2, y + h / 2);
  ctx.rotate(angle);
  ctx.shadowColor = "rgba(74,47,29,0.28)";
  ctx.shadowBlur = 14;
  ctx.shadowOffsetY = 5;
  ctx.fillStyle = "#fbf8f1";
  ctx.fillRect(-width / 2, -h / 2, width, h);
  ctx.shadowColor = "transparent";
  // Photo paper: very slight warm sheen.
  const sheen = ctx.createLinearGradient(-width / 2, -h / 2, width / 2, h / 2);
  sheen.addColorStop(0, "rgba(255,255,255,0.25)");
  sheen.addColorStop(1, "rgba(200,180,150,0.12)");
  ctx.fillStyle = sheen;
  ctx.fillRect(-width / 2, -h / 2, width, h);

  const ix = -width / 2 + pad;
  const iy = -h / 2 + pad;
  if (image instanceof HTMLImageElement) {
    // Cover-fit an uploaded photo.
    const s = Math.max(imgW / image.width, imgH / image.height);
    const sw = imgW / s;
    const sh = imgH / s;
    ctx.drawImage(image, (image.width - sw) / 2, (image.height - sh) / 2, sw, sh, ix, iy, imgW, imgH);
  } else {
    ctx.drawImage(image, ix, iy, imgW, imgH);
  }
  ctx.strokeStyle = "rgba(60,35,15,0.12)";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(ix, iy, imgW, imgH);

  if (photo.caption) {
    ctx.fillStyle = INK_SOFT;
    ctx.font = `500 ${Math.round(width * 0.1)}px ${fonts.hand}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(photo.caption, 0, h / 2 - capH / 2 - pad * 0.3, imgW);
  }

  if (tape === "top") {
    drawTape(ctx, (seed % 3) * 6 - 6, -h / 2 + 2, width * 0.38, width * 0.095, -angle * 0.8 + 0.05, seed);
  } else {
    drawTape(ctx, -width / 2 + width * 0.07, -h / 2 + width * 0.06, width * 0.3, width * 0.085, -0.72, seed);
    drawTape(ctx, width / 2 - width * 0.07, h / 2 - width * 0.06, width * 0.3, width * 0.085, -0.72, seed + 3);
  }
  ctx.restore();
  return y + h;
}

/* ------------------------------------------------------------------------ */
/* Pages                                                                     */
/* ------------------------------------------------------------------------ */

/** Page canvas size in px (aspect matches PAGE_W : PAGE_H). */
export const PAGE_PX_W = 1000;
export const PAGE_PX_H = 1300;

function pageNumber(ctx: Ctx, n: number, side: "left" | "right", fonts: PageFonts) {
  ctx.fillStyle = INK_FAINT;
  ctx.font = `italic 400 26px ${fonts.display}`;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = side === "left" ? "left" : "right";
  ctx.fillText(String(n), side === "left" ? 96 : PAGE_PX_W - 96, PAGE_PX_H - 74);
}

/** Left page: year, title, storybook text, tellers and taped-in photos. */
export async function drawStoryPage(spread: BookSpread, page: number, fonts: PageFonts, seed: number) {
  const W = PAGE_PX_W;
  const H = PAGE_PX_H;
  const { c, ctx } = canvas(W, H);
  paper(ctx, W, H, { seed, gutter: "right" });
  const left = 96;
  const right = W - 134;
  const width = right - left;

  // Year, handwritten in the accent ink.
  ctx.save();
  ctx.translate(left - 4, 168);
  ctx.rotate(-0.035);
  ctx.fillStyle = ACCENT;
  ctx.font = `600 76px ${fonts.hand}`;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  ctx.fillText(String(spread.year), 0, 0);
  ctx.restore();

  // Title.
  ctx.fillStyle = INK;
  ctx.font = `600 64px ${fonts.display}`;
  setSpacing(ctx, -0.8);
  ctx.textAlign = "left";
  const titleLines = wrap(ctx, spread.title, width);
  let y = 262;
  for (const line of titleLines) {
    ctx.fillText(line, left, y);
    y += 72;
  }
  setSpacing(ctx, 0);

  // A short stitched rule in thread.
  y += 8;
  drawStitchLine(ctx, left + 2, y, left + 190, y, ACCENT, 3.2, 16, 11);
  y += 58;

  // Storybook text.
  ctx.fillStyle = INK_SOFT;
  ctx.font = `400 31px ${fonts.display}`;
  const body = wrap(ctx, spread.text, width);
  for (const line of body) {
    ctx.fillText(line, left, y);
    y += 46;
  }

  if (spread.tellers?.length) {
    y += 14;
    ctx.fillStyle = INK_FAINT;
    ctx.font = `500 38px ${fonts.hand}`;
    const names = spread.tellers.length > 1
      ? `${spread.tellers.slice(0, -1).join(", ")} & ${spread.tellers[spread.tellers.length - 1]}`
      : spread.tellers[0];
    ctx.fillText(`— as told by ${names}`, left, y);
    y += 20;
  }

  // Photos, taped into the space that is left.
  const photos = spread.photos.slice(0, 2);
  const top = Math.max(y + 46, 700);
  if (photos.length === 1) {
    const pw = Math.min(520, (H - 150 - top) * (photos[0].aspect ?? 4 / 3) * 0.9);
    await drawPhoto(ctx, photos[0], left + (width - pw) / 2 + 10, top, pw, -0.03, seed + 1, fonts, "top");
  } else if (photos.length >= 2) {
    const room = H - 140 - top;
    const a0 = photos[0].aspect ?? 4 / 3;
    const a1 = photos[1].aspect ?? 4 / 3;
    const w0 = Math.min(430, room * a0 * 0.82);
    const w1 = Math.min(330, room * a1 * 0.72);
    await drawPhoto(ctx, photos[0], left - 6, top, w0, -0.055, seed + 1, fonts, "top");
    await drawPhoto(ctx, photos[1], right - w1 + 8, top + Math.min(70, room * 0.16), w1, 0.06, seed + 2, fonts, "corners");
  }

  pageNumber(ctx, page, "left", fonts);
  return c;
}

/** Right page: an empty, clearly marked slot where the pop-up stage mounts. */
export function drawStagePage(spread: BookSpread, page: number, fonts: PageFonts, seed: number) {
  const W = PAGE_PX_W;
  const H = PAGE_PX_H;
  const { c, ctx } = canvas(W, H);
  paper(ctx, W, H, { seed, gutter: "left" });

  const x0 = 134;
  const x1 = W - 96;
  const y0 = 104;
  const y1 = H - 132;
  // Pencil-dashed slot outline.
  ctx.save();
  ctx.strokeStyle = "rgba(92,78,67,0.3)";
  ctx.lineWidth = 2.4;
  ctx.setLineDash([14, 11]);
  ctx.beginPath();
  ctx.roundRect(x0, y0, x1 - x0, y1 - y0, 8);
  ctx.stroke();
  ctx.restore();
  // Crop marks in thread colour at each corner.
  ctx.strokeStyle = "rgba(185,87,61,0.55)";
  ctx.lineWidth = 3;
  ctx.lineCap = "round";
  const m = 34;
  for (const [cx, cy, sx, sy] of [
    [x0, y0, 1, 1],
    [x1, y0, -1, 1],
    [x0, y1, 1, -1],
    [x1, y1, -1, -1],
  ]) {
    ctx.beginPath();
    ctx.moveTo(cx - sx * 14, cy + sy * m);
    ctx.lineTo(cx - sx * 14, cy - sy * 14);
    ctx.lineTo(cx + sx * m, cy - sy * 14);
    ctx.stroke();
  }
  // The fold line the pop-up hinges on, faint.
  ctx.save();
  ctx.strokeStyle = "rgba(92,78,67,0.14)";
  ctx.lineWidth = 2;
  ctx.setLineDash([6, 10]);
  ctx.beginPath();
  ctx.moveTo(x0 + 40, (y0 + y1) * 0.58);
  ctx.lineTo(x1 - 40, (y0 + y1) * 0.58);
  ctx.stroke();
  ctx.restore();

  ctx.fillStyle = "rgba(143,129,115,0.75)";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `500 40px ${fonts.hand}`;
  ctx.fillText("the pop-up rises here", (x0 + x1) / 2, (y0 + y1) * 0.5);
  ctx.font = `700 19px ${fonts.sans}`;
  setSpacing(ctx, 4);
  ctx.fillStyle = "rgba(143,129,115,0.6)";
  ctx.fillText(`${spread.title.toUpperCase()} · ${spread.year}`.slice(0, 60), (x0 + x1) / 2, (y0 + y1) * 0.5 + 52);
  setSpacing(ctx, 0);

  pageNumber(ctx, page, "right", fonts);
  return c;
}

/** The first leaf's recto: a quiet title page, seen as the book opens. */
export function drawTitlePage(cover: BookCover, fonts: PageFonts, seed: number) {
  const W = PAGE_PX_W;
  const H = PAGE_PX_H;
  const { c, ctx } = canvas(W, H);
  paper(ctx, W, H, { seed, gutter: "left" });
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  const cx = W / 2 + 18;
  if (cover.kicker) {
    ctx.fillStyle = INK_FAINT;
    ctx.font = `700 21px ${fonts.sans}`;
    setSpacing(ctx, 5);
    ctx.fillText(cover.kicker.toUpperCase(), cx, H * 0.4);
    setSpacing(ctx, 0);
  }
  ctx.fillStyle = INK;
  ctx.font = `600 86px ${fonts.display}`;
  ctx.fillText(cover.title, cx, H * 0.4 + 112);
  drawStitchLine(ctx, cx - 90, H * 0.4 + 168, cx + 90, H * 0.4 + 168, ACCENT, 3.2, 16, 11);
  ctx.fillStyle = INK_SOFT;
  ctx.font = `500 44px ${fonts.hand}`;
  ctx.fillText("a pop-up storybook", cx, H * 0.4 + 240);
  return c;
}

export function drawBlankPage(seed: number, gutter: "left" | "right") {
  const { c, ctx } = canvas(PAGE_PX_W / 2, PAGE_PX_H / 2);
  paper(ctx, PAGE_PX_W / 2, PAGE_PX_H / 2, { seed, gutter });
  return c;
}

/* ------------------------------------------------------------------------ */
/* Cover, endpapers, edges                                                   */
/* ------------------------------------------------------------------------ */

const KRAFT = "#b58c62";
const SPINE_CLOTH = "#7a3a2c";
const THREAD = "#f1e6cf";

/**
 * The cover's outside: kraft card, an oxblood cloth tape over the spine held
 * with a whip stitch, and a pasted, taped paper label. The spine runs down
 * the canvas's left edge. `w` × `h` matches the board's aspect.
 */
export function drawCoverArt(cover: BookCover, fonts: PageFonts, w: number, h: number) {
  const { c, ctx } = canvas(w, h);
  paper(ctx, w, h, { seed: 41, tone: KRAFT });
  // Kraft is darker and more flecked than page paper.
  const r = rng(43);
  for (let i = 0; i < (w * h) / 900; i++) {
    ctx.fillStyle = r() > 0.6 ? "rgba(255,240,210,0.12)" : "rgba(70,45,25,0.1)";
    ctx.fillRect(r() * w, r() * h, 1 + r() * 2, 1 + r() * 1.5);
  }

  // Cloth spine tape with a fine weave.
  const tapeW = Math.round(w * 0.14);
  ctx.fillStyle = SPINE_CLOTH;
  ctx.fillRect(0, 0, tapeW, h);
  ctx.globalAlpha = 0.16;
  for (let x = 0; x < tapeW; x += 3) {
    ctx.fillStyle = x % 6 ? "#000" : "#fff";
    ctx.fillRect(x, 0, 1, h);
  }
  for (let y = 0; y < h; y += 3) {
    ctx.fillStyle = y % 6 ? "#000" : "#fff";
    ctx.fillRect(0, y, tapeW, 1);
  }
  ctx.globalAlpha = 1;
  // Tape edge: slightly frayed, with a shadow onto the card.
  const edge = ctx.createLinearGradient(tapeW, 0, tapeW + 14, 0);
  edge.addColorStop(0, "rgba(50,25,15,0.35)");
  edge.addColorStop(1, "rgba(50,25,15,0)");
  ctx.fillStyle = edge;
  ctx.fillRect(tapeW, 0, 14, h);
  ctx.strokeStyle = "rgba(122,58,44,0.8)";
  ctx.lineWidth = 1;
  for (let y = 0; y < h; y += 2 + r() * 3) {
    ctx.beginPath();
    ctx.moveTo(tapeW - 1, y);
    ctx.lineTo(tapeW + 1 + r() * 3, y + (r() - 0.5) * 2);
    ctx.stroke();
  }
  // Whip stitches over the tape's edge, and a running stitch down the spine.
  const step = h / 15;
  for (let i = 0; i < 15; i++) {
    const y = step * (i + 0.5);
    const xa = tapeW * 0.2;
    const xb = tapeW + 16;
    ctx.fillStyle = "rgba(40,25,15,0.45)";
    ctx.beginPath();
    ctx.arc(xb, y + step * 0.18, 3.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(40,25,15,0.3)";
    ctx.lineWidth = 4.2;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(xa + 1, y - step * 0.18 + 2);
    ctx.lineTo(xb + 1, y + step * 0.18 + 2);
    ctx.stroke();
    ctx.strokeStyle = THREAD;
    ctx.lineWidth = 3.6;
    ctx.beginPath();
    ctx.moveTo(xa, y - step * 0.18);
    ctx.lineTo(xb, y + step * 0.18);
    ctx.stroke();
    ctx.strokeStyle = "rgba(255,255,255,0.5)";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(xa, y - step * 0.18 - 1);
    ctx.lineTo(xb, y + step * 0.18 - 1);
    ctx.stroke();
  }

  // Pasted label.
  const lw = w * 0.62;
  const lh = h * 0.34;
  const lx = tapeW + (w - tapeW - lw) / 2;
  const ly = h * 0.2;
  ctx.save();
  ctx.translate(lx + lw / 2, ly + lh / 2);
  ctx.rotate(-0.018);
  ctx.shadowColor = "rgba(50,30,15,0.35)";
  ctx.shadowBlur = 10;
  ctx.shadowOffsetY = 3;
  ctx.fillStyle = "#f3ead8";
  ctx.beginPath();
  const lr = rng(47);
  const pts = 60;
  for (let i = 0; i <= pts; i++) {
    // Deckled outline.
    const t = i / pts;
    const per = 2 * (lw + lh);
    let d = t * per;
    let px: number;
    let py: number;
    if (d < lw) [px, py] = [d - lw / 2, -lh / 2];
    else if ((d -= lw) < lh) [px, py] = [lw / 2, d - lh / 2];
    else if ((d -= lh) < lw) [px, py] = [lw / 2 - d, lh / 2];
    else [px, py] = [-lw / 2, lh / 2 - (d - lw)];
    const j = (lr() - 0.5) * 3;
    if (i === 0) ctx.moveTo(px + j, py + j);
    else ctx.lineTo(px + j, py + j);
  }
  ctx.closePath();
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.clip();
  paper(ctx, lw, lh, { seed: 49 });
  ctx.restore();

  ctx.save();
  ctx.translate(lx + lw / 2, ly + lh / 2);
  ctx.rotate(-0.018);
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  if (cover.kicker) {
    ctx.fillStyle = INK_FAINT;
    ctx.font = `700 ${Math.round(w * 0.019)}px ${fonts.sans}`;
    setSpacing(ctx, Math.round(w * 0.005));
    ctx.fillText(cover.kicker.toUpperCase(), 0, -lh * 0.22);
    setSpacing(ctx, 0);
  }
  ctx.fillStyle = INK;
  ctx.font = `600 ${Math.round(w * 0.082)}px ${fonts.display}`;
  ctx.fillText(cover.title, 0, lh * 0.07);
  drawStitchLine(ctx, -lw * 0.16, lh * 0.19, lw * 0.16, lh * 0.19, ACCENT, 3, 14, 10);
  if (cover.subtitle) {
    ctx.fillStyle = INK_SOFT;
    ctx.font = `500 ${Math.round(w * 0.042)}px ${fonts.hand}`;
    ctx.fillText(cover.subtitle, 0, lh * 0.37);
  }
  ctx.restore();
  // Tape holding the label down at two corners.
  drawTape(ctx, lx + 8, ly + 6, lw * 0.26, h * 0.032, -0.62, 51, "rgba(233,220,189,0.85)");
  drawTape(ctx, lx + lw - 8, ly + lh - 6, lw * 0.26, h * 0.032, -0.62, 53, "rgba(233,220,189,0.85)");

  // Worn edges.
  const vg = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.4, w / 2, h / 2, Math.max(w, h) * 0.75);
  vg.addColorStop(0, "rgba(0,0,0,0)");
  vg.addColorStop(1, "rgba(60,35,15,0.28)");
  ctx.fillStyle = vg;
  ctx.fillRect(0, 0, w, h);
  return c;
}

/** Endpaper on the inside of the boards: small sprigs on cream. */
export function drawEndpaper(w: number, h: number) {
  const { c, ctx } = canvas(w, h);
  paper(ctx, w, h, { seed: 61, tone: "#e9dcc2" });
  const r = rng(63);
  const cell = w / 9;
  for (let gy = 0; gy < h / cell + 1; gy++) {
    for (let gx = 0; gx < 10; gx++) {
      const x = gx * cell + (gy % 2 ? cell / 2 : 0) + (r() - 0.5) * cell * 0.2;
      const y = gy * cell + (r() - 0.5) * cell * 0.2;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(r() * Math.PI * 2);
      ctx.strokeStyle = "rgba(127,152,115,0.45)";
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(0, -cell * 0.22);
      ctx.quadraticCurveTo(cell * 0.05, 0, 0, cell * 0.22);
      ctx.stroke();
      ctx.fillStyle = gx % 3 === 0 ? "rgba(185,87,61,0.32)" : "rgba(127,152,115,0.38)";
      for (const s of [-1, 1]) {
        ctx.beginPath();
        ctx.ellipse(s * cell * 0.06, -cell * 0.05 * s, cell * 0.06, cell * 0.025, s * 0.6, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
  }
  return c;
}

/** Page-block edge: many sheets, so fine uneven stripes across v. */
export function drawPageEdges() {
  const w = 64;
  const h = 512;
  const { c, ctx } = canvas(w, h);
  const r = rng(71);
  let y = 0;
  while (y < h) {
    const t = 1 + Math.floor(r() * 3);
    const shade = r();
    const base = shade > 0.85 ? [214, 200, 174] : [236 - shade * 14, 226 - shade * 14, 206 - shade * 16];
    ctx.fillStyle = `rgb(${base[0]},${base[1]},${base[2]})`;
    ctx.fillRect(0, y, w, t);
    if (r() > 0.55) {
      ctx.fillStyle = "rgba(120,90,60,0.18)";
      ctx.fillRect(0, y + t - 0.5, w, 0.6);
    }
    y += t;
  }
  // A little wobble along the edge.
  ctx.globalAlpha = 0.08;
  for (let i = 0; i < 40; i++) {
    ctx.fillStyle = r() > 0.5 ? "#fff" : "#6b4c30";
    ctx.fillRect(r() * w, 0, 1 + r() * 2, h);
  }
  ctx.globalAlpha = 1;
  return c;
}

/** Soft dark blob for the contact shadow under the book. */
export function drawContactShadow() {
  const { c, ctx } = canvas(256, 256);
  ctx.filter = "blur(20px)";
  ctx.fillStyle = "rgba(0,0,0,1)";
  ctx.fillRect(44, 44, 168, 168);
  return c;
}

/** Cloth over the spine (seen on the closed book's side), with whip stitches. */
export function drawSpineCloth() {
  const w = 128;
  const h = 1024;
  const { c, ctx } = canvas(w, h);
  ctx.fillStyle = SPINE_CLOTH;
  ctx.fillRect(0, 0, w, h);
  ctx.globalAlpha = 0.16;
  for (let x = 0; x < w; x += 3) {
    ctx.fillStyle = x % 6 ? "#000" : "#fff";
    ctx.fillRect(x, 0, 1, h);
  }
  for (let y = 0; y < h; y += 3) {
    ctx.fillStyle = y % 6 ? "#000" : "#fff";
    ctx.fillRect(0, y, w, 1);
  }
  ctx.globalAlpha = 1;
  const step = h / 15;
  ctx.lineCap = "round";
  for (let i = 0; i < 15; i++) {
    const y = step * (i + 0.5);
    ctx.strokeStyle = "rgba(40,25,15,0.35)";
    ctx.lineWidth = 7;
    ctx.beginPath();
    ctx.moveTo(-4, y + 3);
    ctx.lineTo(w + 4, y - step * 0.3 + 3);
    ctx.stroke();
    ctx.strokeStyle = THREAD;
    ctx.lineWidth = 6;
    ctx.beginPath();
    ctx.moveTo(-4, y);
    ctx.lineTo(w + 4, y - step * 0.3);
    ctx.stroke();
  }
  // Worn, rounded spine: lighter where it catches the light.
  const g = ctx.createLinearGradient(0, 0, w, 0);
  g.addColorStop(0, "rgba(0,0,0,0.25)");
  g.addColorStop(0.5, "rgba(255,230,200,0.1)");
  g.addColorStop(1, "rgba(0,0,0,0.25)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  return c;
}
