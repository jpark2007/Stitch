/**
 * Canvas 2D drawing primitives for thread: twisted, fibrous yarn ribbons,
 * needle holes in the paper, tied knots and French knots.
 *
 * Every primitive draws in screen pixels. Each yarn is built as a few
 * batched paths (shadow, body, ply twist, sheen) so a whole thread costs a
 * handful of stroke calls per frame however many stitches it has.
 */

export type RGB = [number, number, number];

export function hexToRgb(hex: string): RGB {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.replace(/(.)/g, "$1$1") : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function mix(a: RGB, b: RGB, k: number): RGB {
  return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
}

export function rgba(c: RGB, a = 1): string {
  return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
}

const BLACK: RGB = [38, 26, 18];
const WHITE: RGB = [255, 250, 238];
export const PAPER: RGB = [245, 238, 225];

/** The colours one yarn needs, derived once per colour and dim level. */
export interface YarnInk {
  body: string;
  ply: string;
  sheen: string;
  shadow: string;
  hole: string;
}

const inkCache = new Map<string, YarnInk>();

/** `dim` 0..1 fades the yarn towards the paper (for non-highlighted threads). */
export function yarnInk(hex: string, dim = 0): YarnInk {
  const q = Math.round(dim * 20) / 20;
  const key = hex + q;
  const hit = inkCache.get(key);
  if (hit) return hit;
  const base = mix(hexToRgb(hex), PAPER, q * 0.72);
  const ink: YarnInk = {
    body: rgba(base),
    ply: rgba(mix(base, BLACK, 0.42), 0.55 * (1 - q * 0.5)),
    sheen: rgba(mix(base, WHITE, 0.55), 0.6 * (1 - q * 0.6)),
    shadow: `rgba(74,47,29,${0.26 * (1 - q * 0.75)})`,
    hole: `rgba(58,38,24,${0.5 * (1 - q * 0.7)})`,
  };
  inkCache.set(key, ink);
  return ink;
}

/** A run of points (flat x,y pairs) that is drawn as one piece of yarn. */
export type Polyline = number[];

/**
 * Draw yarn along polylines. `width` is the ribbon width in px; `pitch`
 * the twist spacing. `lod` 0 = far (body + shadow only), 1 = mid (+ twist),
 * 2 = close (+ sheen and fibres).
 */
export function drawYarn(
  ctx: CanvasRenderingContext2D,
  runs: Polyline[],
  ink: YarnInk,
  width: number,
  lod: number,
  closed = false,
) {
  if (runs.length === 0) return;
  const trace = (dx: number, dy: number) => {
    ctx.beginPath();
    for (const r of runs) {
      if (r.length < 4) continue;
      ctx.moveTo(r[0] + dx, r[1] + dy);
      for (let i = 2; i < r.length; i += 2) ctx.lineTo(r[i] + dx, r[i + 1] + dy);
      if (closed) ctx.closePath();
    }
  };

  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  // Shadow: the thread sits a hair above the paper.
  trace(width * 0.18, width * 0.38);
  ctx.strokeStyle = ink.shadow;
  ctx.lineWidth = width * 1.25;
  ctx.stroke();

  // Body.
  trace(0, 0);
  ctx.strokeStyle = ink.body;
  ctx.lineWidth = width;
  ctx.stroke();

  if (lod < 1 || width < 2.2) return;

  // Ply twist: slanted strokes across the ribbon, every `pitch` px of arc.
  const pitch = Math.max(2.4, width * 0.95);
  const half = width * 0.5;
  const lean = width * 0.42;
  ctx.beginPath();
  for (const r of runs) {
    let carry = pitch * 0.5;
    const n = r.length / 2 + (closed ? 1 : 0);
    for (let i = 1; i < n; i++) {
      const j0 = (i - 1) % (r.length / 2);
      const j1 = i % (r.length / 2);
      const x0 = r[j0 * 2];
      const y0 = r[j0 * 2 + 1];
      const x1 = r[j1 * 2];
      const y1 = r[j1 * 2 + 1];
      const dx = x1 - x0;
      const dy = y1 - y0;
      const len = Math.hypot(dx, dy);
      if (len < 1e-4) continue;
      const tx = dx / len;
      const ty = dy / len;
      const nx = -ty;
      const ny = tx;
      let s = carry;
      while (s < len) {
        const px = x0 + tx * s;
        const py = y0 + ty * s;
        ctx.moveTo(px + nx * half - tx * lean, py + ny * half - ty * lean);
        ctx.lineTo(px - nx * half + tx * lean, py - ny * half + ty * lean);
        s += pitch;
      }
      carry = s - len;
    }
  }
  ctx.strokeStyle = ink.ply;
  ctx.lineWidth = Math.max(0.7, width * 0.2);
  ctx.lineCap = "butt";
  ctx.stroke();
  ctx.lineCap = "round";

  if (lod < 2) return;

  // Sheen: a thin light line riding the upper edge of the ribbon.
  ctx.beginPath();
  for (const r of runs) {
    if (r.length < 4) continue;
    for (let i = 0; i < r.length; i += 2) {
      const a = Math.max(0, i - 2);
      const b = Math.min(r.length - 2, i + 2);
      const dx = r[b] - r[a];
      const dy = r[b + 1] - r[a + 1];
      const len = Math.hypot(dx, dy) || 1;
      // Normal pointing "up-left" on screen.
      let nx = dy / len;
      let ny = -dx / len;
      if (ny > 0) {
        nx = -nx;
        ny = -ny;
      }
      const x = r[i] + nx * width * 0.2;
      const y = r[i + 1] + ny * width * 0.2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    if (closed) ctx.closePath();
  }
  ctx.strokeStyle = ink.sheen;
  ctx.lineWidth = Math.max(0.6, width * 0.2);
  ctx.stroke();
}

/** Needle holes where the thread goes through the paper. Flat x,y pairs. */
export function drawHoles(ctx: CanvasRenderingContext2D, pts: number[], r: number, ink: YarnInk) {
  if (pts.length === 0) return;
  ctx.beginPath();
  for (let i = 0; i < pts.length; i += 2) {
    ctx.moveTo(pts[i] + r, pts[i + 1]);
    ctx.arc(pts[i], pts[i + 1], r, 0, Math.PI * 2);
  }
  ctx.fillStyle = ink.hole;
  ctx.fill();
}

/* ------------------------------------------------------------------ */
/* Knots                                                               */
/* ------------------------------------------------------------------ */

/** Small deterministic PRNG so every knot is tied the same way each frame. */
export function hashSeed(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}

function ellipseRun(cx: number, cy: number, rx: number, ry: number, rot: number, steps = 20): Polyline {
  const out: Polyline = [];
  const c = Math.cos(rot);
  const s = Math.sin(rot);
  for (let i = 0; i < steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    const x = Math.cos(a) * rx;
    const y = Math.sin(a) * ry;
    out.push(cx + x * c - y * s, cy + x * s + y * c);
  }
  return out;
}

/** The soft cast shadow under a knot, as if it sits a little proud. */
function knotShadow(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, alpha: number) {
  const g = ctx.createRadialGradient(x + r * 0.15, y + r * 0.35, 0, x + r * 0.15, y + r * 0.35, r * 1.6);
  g.addColorStop(0, `rgba(74,47,29,${0.28 * alpha})`);
  g.addColorStop(1, "rgba(74,47,29,0)");
  ctx.fillStyle = g;
  ctx.fillRect(x - r * 2, y - r * 2, r * 4, r * 4);
}

/**
 * A knot tied from several threads: one loop per person fanned around the
 * centre, then a tight wrap across the middle. `inks` are in lane order.
 */
export function drawTiedKnot(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  inks: YarnInk[],
  width: number,
  seed: number,
  lod: number,
  alpha = 1,
) {
  const k = inks.length;
  knotShadow(ctx, x, y, r, alpha);
  const spin = seed * Math.PI;
  // Loops: petals around the centre, one per participant.
  for (let i = 0; i < k; i++) {
    const a = spin + (i / k) * Math.PI + (k === 2 ? Math.PI / 4 : 0);
    const off = r * 0.42;
    for (const side of [1, -1]) {
      const cx = x + Math.cos(a) * off * side;
      const cy = y + Math.sin(a) * off * side;
      drawYarn(ctx, [ellipseRun(cx, cy, r * 0.56, r * 0.3, a, 18)], inks[i], width, lod, true);
    }
  }
  // The wrap: short tight turns over the middle, alternating colours.
  const turns = Math.min(3, k + 1);
  for (let j = 0; j < turns; j++) {
    const a = spin + Math.PI / 2 + (j - (turns - 1) / 2) * 0.42;
    const ink = inks[(j + k - 1) % k];
    const ca = Math.cos(a);
    const sa = Math.sin(a);
    const L = r * 0.5;
    const bow = r * 0.12;
    const run: Polyline = [];
    for (let s = 0; s <= 6; s++) {
      const u = s / 6 - 0.5;
      const along = u * 2 * L;
      const across = bow * (1 - 4 * u * u);
      run.push(x + ca * along - sa * across, y + sa * along + ca * across);
    }
    drawYarn(ctx, [run], ink, width * 1.15, lod);
  }
}

/** A French knot: a tight little coil of one thread, for single-person moments. */
export function drawFrenchKnot(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  ink: YarnInk,
  width: number,
  seed: number,
  lod: number,
  alpha = 1,
) {
  knotShadow(ctx, x, y, r * 0.9, alpha);
  const spin = seed * Math.PI * 2;
  for (let i = 0; i < 3; i++) {
    const a = spin + (i / 3) * Math.PI * 2;
    const cx = x + Math.cos(a) * r * 0.32;
    const cy = y + Math.sin(a) * r * 0.32;
    drawYarn(ctx, [ellipseRun(cx, cy, r * 0.5, r * 0.4, a + 0.6, 14)], ink, width, lod, true);
  }
}
