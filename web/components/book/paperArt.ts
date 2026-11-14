/**
 * The basics every drawn page shares: seeded randomness, canvases, ink
 * colours, page fonts, text wrapping, the paper itself, tape and thread.
 * Browser-only (call from effects). No image assets.
 */

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

export function canvas(w: number, h: number) {
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
export const PAPER = "#f7f0e2";

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

export type Ctx = CanvasRenderingContext2D;

export function setSpacing(ctx: Ctx, px: number) {
  // Canvas letterSpacing is Chromium/Safari/Firefox 2023+; harmless if absent.
  (ctx as Ctx & { letterSpacing?: string }).letterSpacing = `${px}px`;
}

export function wrap(ctx: Ctx, text: string, maxW: number): string[] {
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

export interface PaperOptions {
  seed: number;
  /** Which side the spine is on, for the gutter shade. */
  gutter?: "left" | "right";
  tone?: string;
}

export function paper(ctx: Ctx, w: number, h: number, o: PaperOptions) {
  const r = rng(o.seed);
  ctx.fillStyle = o.tone ?? PAPER;
  ctx.fillRect(0, 0, w, h);

  // Soft mottling: the sheet is never one flat colour.
  for (let i = 0; i < 9; i++) {
    const x = r() * w;
    const y = r() * h;
    const rad = (0.3 + r() * 0.4) * Math.max(w, h);
    const g = ctx.createRadialGradient(x, y, 0, x, y, rad);
    const light = r() > 0.4;
    // Barely there: any stronger and it reads as stains under the lamp.
    g.addColorStop(0, light ? "rgba(255,252,244,0.07)" : "rgba(196,163,122,0.025)");
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
/* Tape and thread                                                           */
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
export function drawStitchLine(ctx: Ctx, x0: number, y0: number, x1: number, y1: number, color: string, width = 3, dash = 15, gap = 10) {
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
