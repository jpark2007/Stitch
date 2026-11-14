/**
 * Draws one frame of the threads screen on a 2D canvas and returns the
 * screen geometry the UI needs for hit-testing and for placing DOM labels.
 *
 * Level of detail by zoom:
 *  - far:   laid yarn (continuous), knots, decade labels
 *  - mid:   + ply twist; the yarn opens into a running stitch with needle holes
 *  - close: + sheen, loose fibres, moment titles (placed by the UI)
 */

import { toX, type Camera } from "./camera";
import { relax, strandAt, type Knot, type Shape, type Strand, type ThreadsModel } from "./model";
import { drawCrossStitch, drawFrenchKnot, drawHoles, drawTiedKnot, drawYarn, hashSeed, yarnInk, type Polyline } from "./yarn";

export interface Layout {
  width: number;
  height: number;
  /** y (px) of lane 0. */
  top: number;
  /** px between lanes. */
  gap: number;
  /** y (px) of the year axis baseline. */
  axisY: number;
}

export function makeLayout(width: number, height: number, lanes: number): Layout {
  const compact = width < 640;
  const top = compact ? 168 : 182;
  // On phones the zoom buttons sit below the axis.
  const axisY = height - (compact ? 104 : 74);
  const avail = axisY - 44 - top;
  const gap = lanes > 1 ? Math.min(compact ? 70 : 80, avail / (lanes - 1)) : 0;
  // Centre the lane block in the space between header and axis.
  const block = gap * (lanes - 1);
  return { width, height, top: top + Math.max(0, (avail - block) / 2), gap, axisY };
}

export interface FrameState {
  cam: Camera;
  layout: Layout;
  /** Per strand (model order): 0 = full colour, 1 = faded. */
  dim: number[];
  /** Per strand: 0..1 how highlighted (thicker). */
  lift: number[];
  /** Knot index under the pointer, or -1. */
  hoverKnot: number;
  /** 0..1 hover swell per knot. */
  swell: number[];
  /** Intro: threads are sewn up to this year. */
  reveal: number;
  /** Per knot 0..1: how far it has popped in since the needle reached it. */
  pop: number[];
  fonts: { hand: string; sans: string; serif: string };
}

export interface KnotHit {
  index: number;
  x: number;
  y: number;
  r: number;
  visible: boolean;
}

export interface FrameGeometry {
  /** Screen polyline per strand (flat x,y), for hit tests. */
  strands: Float32Array[];
  strandLens: number[];
  knots: KnotHit[];
  /** Name label boxes, so titles can avoid them. */
  names: { x0: number; y0: number; x1: number; y1: number }[];
  lod: number;
}

export function threadWidth(s: number): number {
  return Math.min(4.4, Math.max(2.6, 2.3 + s * 0.011));
}

export function knotRadius(s: number, people: number): number {
  const base = Math.min(12.5, Math.max(8, 5.6 + s * 0.04));
  return people > 1 ? base * (1 + Math.min(0.4, (people - 2) * 0.13)) : base * 0.82;
}

function lodFor(s: number): number {
  if (s >= 60) return 2;
  if (s >= 12) return 1;
  return 0;
}

/**
 * The share of each stitch period left as bare paper. Round caps eat half
 * the yarn width at each end, so the cut is widened by that much to leave a
 * visible gap: a hairline far out, a proper running stitch close in.
 */
function stitchGap(s: number, periodPx: number, width: number): number {
  const u = Math.min(1, Math.max(0, (s - 20) / 50));
  const visible = 2.2 + u * u * (3 - 2 * u) * (0.11 * periodPx - 2.2);
  return Math.min(0.4, (visible + width * 0.95) / periodPx);
}

function rand(i: number, seed: number): number {
  let h = Math.imul(i ^ Math.floor(seed * 4294967296), 2654435761) >>> 0;
  h ^= h >>> 15;
  h = Math.imul(h, 2246822519) >>> 0;
  h ^= h >>> 13;
  return (h >>> 0) / 4294967296;
}

const scratch: Float32Array[] = [];

export function drawFrame(ctx: CanvasRenderingContext2D, model: ThreadsModel, st: FrameState): FrameGeometry {
  const { cam, layout } = st;
  const { width: W, height: H } = layout;
  const s = cam.s;
  const lod = lodFor(s);
  const shape: Shape = { s, gap: layout.gap };
  relax(model, shape);

  ctx.clearRect(0, 0, W, H);
  const laneY = (l: number) => layout.top + l * layout.gap;
  const X = (t: number) => toX(t, cam, W);
  const tLeft = cam.cx - W / 2 / s;
  const tRight = cam.cx + W / 2 / s;

  drawAxis(ctx, st, tLeft, tRight, model);

  /* ---------------- threads ---------------- */
  const w0 = threadWidth(s);
  const dt = 2.5 / s; // sample every ~2.5px
  // Running stitch: the period in years is a power of two, so while zooming
  // stitches only ever split or merge in place, they never slide.
  const period = Math.pow(2, Math.ceil(Math.log2(26 / s)));
  const knotT = model.knots.map((k) => k.t);
  const knotClear = (knotRadius(s, 3) * 1.3) / s; // years either side of a knot left unbroken

  const geom: FrameGeometry = { strands: [], strandLens: [], knots: [], names: [], lod };
  const starts: { i: number; x: number; y: number; ink: ReturnType<typeof yarnInk>; width: number }[] = [];

  // Faded strands first so highlighted ones sit on top.
  const order = model.strands.map((_, i) => i).sort((a, b) => st.dim[b] - st.dim[a] || st.lift[a] - st.lift[b]);

  for (const i of order) {
    const strand = model.strands[i];
    const ink = yarnInk(strand.person.color, st.dim[i]);
    const width = w0 + st.lift[i] * 1.2;
    const t0 = Math.max(strand.start.t, tLeft - 30 / s);
    const t1 = Math.min(model.tNow, tRight + 30 / s, st.reveal);
    if (t1 <= t0) {
      geom.strands[i] = new Float32Array(0);
      geom.strandLens[i] = 0;
      continue;
    }
    const nSamples = Math.ceil((t1 - t0) / dt) + 1;
    let buf = scratch[i];
    if (!buf || buf.length < nSamples * 3) buf = scratch[i] = new Float32Array(nSamples * 3 + 384);
    // Stitch phase: years along, plus vertical travel converted to years at
    // this zoom, so steep swings get as many stitches as their length needs.
    const travelToYears = layout.gap / s;
    const seed = hashSeed(strand.person.id);
    const off = seed * 10;
    const phase = buf.subarray(nSamples * 2, nSamples * 3);
    for (let k = 0; k < nSamples; k++) {
      const t = Math.min(t1, t0 + k * dt);
      const smp = strandAt(strand, t, shape);
      buf[k * 2] = X(t);
      buf[k * 2 + 1] = laneY(smp.y);
      phase[k] = t + smp.travel * travelToYears + off;
    }
    geom.strands[i] = buf.subarray(0, nSamples * 2);
    geom.strandLens[i] = nSamples;

    // Cut into stitches (each slightly irregular, as if sewn by hand). Gaps
    // can be narrower than the sample spacing, so every gap edge is found
    // exactly by interpolating between samples.
    const runs: Polyline[] = [];
    const holes: number[] = [];
    let run: Polyline = [];
    const holeOut = width * 0.7;
    const gapFrac = stitchGap(s, period * s, width);
    const gapOf = (cell: number) => gapFrac * (0.8 + rand(cell, seed) * 0.4);
    const tAt = (k: number) => Math.min(t1, t0 + k * dt);
    // A hole just past a stitch end, into the gap: where the needle went in.
    const hole = (x: number, y: number, dx: number, dy: number) => {
      const len = Math.hypot(dx, dy) || 1;
      holes.push(x + (dx / len) * holeOut, y + (dy / len) * holeOut);
    };
    let p0 = phase[0] / period;
    let inGap = p0 - Math.floor(p0) > 1 - gapOf(Math.floor(p0)) && !nearAny(knotT, t0, knotClear);
    // Just past the last gap edge handled. It carries over between samples:
    // an edge that falls exactly on a sample is handled by the next one,
    // rather than skipped (which would drop a whole stitch).
    let pa = p0;
    if (!inGap) run.push(buf[0], buf[1]);
    for (let k = 1; k < nSamples; k++) {
      const p1 = phase[k] / period;
      const xa = buf[k * 2 - 2];
      const ya = buf[k * 2 - 1];
      const xb = buf[k * 2];
      const yb = buf[k * 2 + 1];
      // Walk every gap edge between the two samples.
      for (let guard = 0; guard < 64; guard++) {
        const cell = Math.floor(pa);
        const gapStart = cell + 1 - gapOf(cell);
        const edge = pa < gapStart ? gapStart : cell + 1;
        if (edge >= p1) break;
        const f = (edge - p0) / (p1 - p0 || 1);
        const x = xa + (xb - xa) * f;
        const y = ya + (yb - ya) * f;
        if (edge === gapStart) {
          const t = tAt(k - 1) + dt * f;
          if (!inGap && !nearAny(knotT, t, knotClear)) {
            run.push(x, y);
            if (run.length >= 4) runs.push(run);
            hole(x, y, xb - xa, yb - ya);
            run = [];
            inGap = true;
          }
        } else if (inGap) {
          hole(x, y, xa - xb, ya - yb);
          run = [x, y];
          inGap = false;
        }
        pa = edge + 1e-9;
      }
      if (!inGap) run.push(xb, yb);
      p0 = p1;
    }
    if (run.length >= 4) runs.push(run);

    if (holes.length) drawHoles(ctx, holes, Math.max(0.75, width * 0.28), ink);
    // Where a life begins, the thread is knotted before it is sewn (unless
    // it is born straight into a moment, whose knot says it already).
    if (t0 === strand.start.t && !bornInKnot(strand, model)) starts.push({ i, x: buf[0], y: buf[1], ink, width });
    // ...and where it goes back down, today.
    if (t1 >= model.tNow - 1e-6) {
      const e = (nSamples - 1) * 2;
      drawHoles(ctx, [buf[e] + width * 0.6, buf[e + 1]], Math.max(1.3, width * 0.45), ink);
    }
    drawYarn(ctx, runs, ink, width, lod);
    if (lod >= 2) drawFibres(ctx, runs, ink.body, width, seed);
  }

  /* ---------------- start knots ---------------- */
  const rStart = Math.max(3.4, Math.min(5.6, knotRadius(s, 1) * 0.6));
  for (const k of starts) {
    drawFrenchKnot(ctx, k.x, k.y, rStart * (1 + st.lift[k.i] * 0.15), k.ink, k.width * 0.8, hashSeed(model.strands[k.i].person.id), Math.max(1, lod), 1 - st.dim[k.i] * 0.75);
  }

  /* ---------------- knots ---------------- */
  const involvement = model.knots.map((k) => knotInvolvement(k, model.strands, st.dim));
  const knotOrder = model.knots.map((_, i) => i);
  knotOrder.sort((a, b) => involvement[b] - involvement[a] || (a === st.hoverKnot ? 1 : b === st.hoverKnot ? -1 : 0));
  for (const ki of knotOrder) {
    const k = model.knots[ki];
    const x = X(k.t);
    const y = laneY(k.y);
    const r0 = knotRadius(s, k.people.length);
    const appear = st.pop[ki] ?? 1;
    const pop = appear < 1 ? easeBack(appear) : 1;
    let lifted = 0;
    for (const p of k.people) lifted = Math.max(lifted, st.lift[model.strands.findIndex((q) => q.person.id === p.id)] ?? 0);
    const rHit = r0 * (1 + st.swell[ki] * 0.25 + lifted * 0.16);
    const r = rHit * pop;
    const visible = x > -r0 * 3 && x < W + r0 * 3 && appear > 0;
    geom.knots[ki] = { index: ki, x, y, r: rHit, visible };
    if (!visible || r < 0.5) continue;
    const fade = involvement[ki];
    const width = Math.max(1.9, w0 * 0.85);
    const seed = hashSeed(k.moment.id);
    if (k.people.length === 1) {
      const idx = model.strands.findIndex((q) => q.person.id === k.people[0].id);
      drawCrossStitch(ctx, x, y, r, yarnInk(k.people[0].color, st.dim[idx] ?? 0), width * 1.15, seed, Math.max(1, lod), 1 - fade * 0.75);
    } else {
      // A knot keeps everyone's colour; it fades only when none of its people is lit.
      const inks = k.people.map((p) => {
        const idx = model.strands.findIndex((q) => q.person.id === p.id);
        return yarnInk(p.color, Math.max(fade, (st.dim[idx] ?? 0) * 0.45));
      });
      drawTiedKnot(ctx, x, y, r, inks, width, seed, Math.max(1, lod), 1 - fade * 0.75);
    }
  }

  /* ---------------- names ---------------- */
  drawNames(ctx, model, st, geom, w0);

  return geom;
}

/** True when the thread starts inside its first moment's knot. */
function bornInKnot(strand: Strand, model: ThreadsModel): boolean {
  const id = strand.momentIds[0];
  return id !== undefined && model.knots.some((k) => k.moment.id === id && Math.abs(k.t - strand.start.t) < 1e-6);
}

/** How faded a knot is: 0 as soon as any one of its people is lit. */
function knotInvolvement(k: Knot, strands: Strand[], dim: number[]): number {
  let best = 1;
  for (const p of k.people) {
    const i = strands.findIndex((q) => q.person.id === p.id);
    best = Math.min(best, dim[i] ?? 0);
  }
  return best;
}

function nearAny(ts: number[], t: number, r: number): boolean {
  for (let i = 0; i < ts.length; i++) if (Math.abs(ts[i] - t) < r) return true;
  return false;
}

function easeBack(x: number): number {
  const c = 1.9;
  return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2);
}

/** A few loose fibres standing off the yarn, only at close zoom. */
function drawFibres(ctx: CanvasRenderingContext2D, runs: Polyline[], color: string, width: number, seed: number) {
  ctx.beginPath();
  let n = Math.floor(seed * 1000);
  for (const r of runs) {
    for (let i = 4; i < r.length - 4; i += 12) {
      n = (n * 1103515245 + 12345) & 0x7fffffff;
      const u = n / 0x7fffffff;
      if (u > 0.4) continue;
      const dx = r[i + 2] - r[i - 2];
      const dy = r[i + 3] - r[i - 1];
      const len = Math.hypot(dx, dy) || 1;
      const side = u < 0.2 ? 1 : -1;
      const nx = (-dy / len) * side;
      const ny = (dx / len) * side;
      const tx = dx / len;
      const ty = dy / len;
      const x = r[i] + nx * width * 0.42;
      const y = r[i + 1] + ny * width * 0.42;
      const L = width * (0.5 + u * 1.5);
      ctx.moveTo(x, y);
      ctx.quadraticCurveTo(x + nx * L * 0.6 + tx * L * 0.5, y + ny * L * 0.6 + ty * L * 0.5, x + nx * L + tx * L * 0.15, y + ny * L + ty * L * 0.15);
    }
  }
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.5;
  ctx.lineWidth = 0.55;
  ctx.stroke();
  ctx.globalAlpha = 1;
}

/* ---------------- axis ---------------- */

const STEPS = [1, 2, 5, 10, 20, 50];
/** Kraft twine for the axis: a running stitch, darker where it crosses. */
const KRAFT = "rgba(168,135,98,0.9)";
const KRAFT_DEEP = "rgba(128,96,64,0.95)";
const KRAFT_SHADOW = "rgba(74,47,29,0.16)";
const DASH = [5, 4];

/**
 * The year axis is sewn too: a kraft running stitch along the bottom, a
 * small cross-stitch at every decade, short upright stitches at the other
 * marked years, and old-style serif numerals under the decades.
 */
function drawAxis(ctx: CanvasRenderingContext2D, st: FrameState, tLeft: number, tRight: number, model: ThreadsModel) {
  const { cam, layout } = st;
  const s = cam.s;
  const W = layout.width;
  const minor = STEPS.find((d) => d * s >= 9) ?? 50;
  const major = STEPS.find((d) => d * s >= (W < 640 ? 56 : 62) && d % minor === 0) ?? 50;
  const y = layout.axisY;
  const lo = Math.max(Math.ceil(tLeft / minor) * minor, Math.ceil(model.tMin / minor) * minor);
  const hi = Math.min(tRight, model.tMax);
  const x0 = toX(model.tMin, cam, W);
  const xL = Math.max(-10, x0);
  const xR = Math.min(W + 10, toX(model.tMax, cam, W));

  // Faint pencil guides at major years, behind everything.
  ctx.beginPath();
  for (let t = Math.ceil(lo / major) * major; t <= hi; t += major) {
    const x = Math.round(toX(t, cam, W)) + 0.5;
    ctx.moveTo(x, layout.top - 30);
    ctx.lineTo(x, y - 14);
  }
  ctx.setLineDash([1.5, 7]);
  ctx.strokeStyle = "rgba(47,38,32,0.09)";
  ctx.lineWidth = 1;
  ctx.stroke();

  // The running stitch. Its dashes are pinned to the timeline (not the
  // screen), so they travel with the threads when you pan.
  ctx.setLineDash(DASH);
  ctx.lineDashOffset = (xL - x0) % (DASH[0] + DASH[1]);
  ctx.lineCap = "round";
  for (const [dy, color, w] of [
    [1.1, KRAFT_SHADOW, 1.9],
    [0, KRAFT, 1.6],
  ] as const) {
    ctx.beginPath();
    ctx.moveTo(xL, y + dy);
    ctx.lineTo(xR, y + dy);
    ctx.strokeStyle = color;
    ctx.lineWidth = w;
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.lineDashOffset = 0;

  // Stitches at the marked years: an X at decades, an upright elsewhere.
  // Year stitches fade in as there is room for them. Once each year has its
  // own label, centred in the year, uprights mark the year boundaries and
  // a decade's X moves to sit over its numeral.
  const mid = major === 1 && s > 70 ? 0.5 : 0;
  const yearInk = Math.min(1, Math.max(0, (minor * s - 18) / 12));
  const cross = new Path2D();
  const upright = new Path2D();
  const faint = new Path2D();
  const xa = W < 640 ? 3.2 : 3.6;
  for (let t = lo; t <= hi; t += minor) {
    const x = toX(t, cam, W);
    if (t % 10 === 0) {
      const cx = mid ? toX(t + mid, cam, W) : x;
      cross.moveTo(cx - xa, y - xa);
      cross.lineTo(cx + xa, y + xa);
      cross.moveTo(cx - xa, y + xa);
      cross.lineTo(cx + xa, y - xa);
      if (mid) {
        upright.moveTo(x, y - 4.5);
        upright.lineTo(x, y + 4.5);
      }
    } else {
      const path = t % major === 0 || t % 5 === 0 ? upright : faint;
      const h = t % 5 === 0 ? 4.5 : 3;
      path.moveTo(x, y - h);
      path.lineTo(x, y + h);
    }
  }
  ctx.translate(0.6, 1.1);
  ctx.strokeStyle = KRAFT_SHADOW;
  ctx.lineWidth = 2;
  ctx.stroke(cross);
  ctx.translate(-0.6, -1.1);
  ctx.strokeStyle = KRAFT_DEEP;
  ctx.lineWidth = 1.7;
  ctx.stroke(cross);
  ctx.lineWidth = 1.4;
  ctx.strokeStyle = KRAFT;
  ctx.stroke(upright);
  if (yearInk > 0) {
    ctx.globalAlpha = yearInk;
    ctx.stroke(faint);
    ctx.globalAlpha = 1;
  }
  ctx.lineCap = "butt";

  // Labels: decades in the storybook serif with old-style figures, other
  // years small in the sans. At single-year spacing a label sits in the
  // middle of its year.
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const compact = W < 640;
  const decadeFont = `400 ${compact ? 13.5 : 14.5}px ${st.fonts.serif}`;
  const yearFont = `600 ${compact ? 11 : 11.5}px ${st.fonts.sans}`;
  for (let t = Math.ceil(lo / major) * major - (mid ? 1 : 0); t <= hi; t += major) {
    if (t < model.tMin) continue;
    const x = toX(t + mid, cam, W);
    const isDecade = t % 10 === 0;
    ctx.font = isDecade ? decadeFont : yearFont;
    ctx.fillStyle = isDecade ? "rgba(92,78,67,0.95)" : "rgba(143,129,115,0.95)";
    const label = isDecade && major >= 10 ? `${t}s` : String(t);
    ctx.fillText(label, x, y + (isDecade ? 10 : 12));
  }
}

/* ---------------- names ---------------- */

/** Does any thread but `self` pass through the box? Samples are evenly spaced in x. */
function crossedBy(g: FrameGeometry, self: number, x0: number, x1: number, y0: number, y1: number): boolean {
  for (let j = 0; j < g.strands.length; j++) {
    if (j === self) continue;
    const pts = g.strands[j];
    const n = g.strandLens[j];
    if (!pts || n < 2) continue;
    const step = pts[2] - pts[0] || 1;
    const k0 = Math.max(0, Math.floor((x0 - pts[0]) / step));
    const k1 = Math.min(n - 1, Math.ceil((x1 - pts[0]) / step));
    for (let k = k0; k <= k1; k++) {
      const y = pts[k * 2 + 1];
      if (y > y0 && y < y1) return true;
    }
  }
  return false;
}

/**
 * Each thread is signed with its person's name, written just above it
 * where it first settles on its own lane (past the knot it may be born
 * into). When that point scrolls off the left, the name rides the margin.
 */
function drawNames(ctx: CanvasRenderingContext2D, model: ThreadsModel, st: FrameState, geom: FrameGeometry, w0: number) {
  const { layout } = st;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  const size = layout.width < 640 ? 19 : 21;
  ctx.font = `600 ${size}px ${st.fonts.hand}`;
  const margin = 16;
  for (let i = 0; i < model.strands.length; i++) {
    const pts = geom.strands[i];
    const n = geom.strandLens[i];
    if (!pts || n < 2) continue;
    const strand = model.strands[i];
    const lanePx = layout.top + strand.lane * layout.gap;
    // First sample where the whole name fits over a level stretch of the
    // thread, clear of knots.
    const w = ctx.measureText(strand.person.name).width;
    const startX = pts[0];
    const right = layout.width - margin;
    // Does a label starting at x sit over level thread, clear of knots?
    const fits = (k: number, x: number) => {
      if (x < margin || x + w > right) return false;
      if (Math.abs(pts[k * 2 + 1] - lanePx) > 2) return false;
      for (let j = k; j < n && pts[j * 2] < x + w; j += 4) {
        if (Math.abs(pts[j * 2 + 1] - lanePx) > 6) return false;
      }
      if (
        geom.knots.some(
          (h) => h && h.visible && h.x + h.r + 8 > x && h.x - h.r - 8 < x + w + 6 && Math.abs(h.y - lanePx) < h.r + 22,
        )
      )
        return false;
      // Nor where another thread runs through the name.
      const yb = lanePx - w0 - 8;
      return !crossedBy(geom, i, x - 2, x + w + 2, yb - size, yb + 3);
    };
    const lead = startX >= margin ? 6 : 0;
    let x = -1;
    let at = -1;
    for (let k = 0; k < n && x < 0; k++) {
      const px = Math.max(margin, pts[k * 2] + lead);
      if (pts[k * 2] + w > right) break;
      if (fits(k, px)) {
        x = px;
        at = k;
      }
    }
    // Nowhere after the start (a late thread near the right edge): sign the
    // last level stretch instead, right-aligned.
    for (let k = n - 1; k >= 0 && x < 0; k -= 2) {
      const px = Math.min(pts[k * 2], right) - w;
      let k0 = k;
      while (k0 > 0 && pts[k0 * 2] > px) k0--;
      if (pts[k0 * 2] < startX) break;
      if (fits(k0, px)) {
        x = px;
        at = k0;
      }
    }
    // Still nowhere (a thread still swinging as it ends): sign its end.
    if (x < 0) {
      const ex = pts[(n - 1) * 2];
      if (ex < right - 4 && ex - w > margin) {
        x = ex - w;
        at = n - 1;
      }
    }
    if (x < 0) continue;
    const y = pts[at * 2 + 1] - w0 - 8;
    const riding = startX < margin;
    geom.names.push({ x0: x, y0: y - size, x1: x + w, y1: y + 4 });
    ctx.globalAlpha = riding ? 0.85 : 1;
    ctx.fillStyle = yarnInk(strand.person.color, st.dim[i]).label;
    ctx.fillText(strand.person.name, x, y);
  }
  ctx.globalAlpha = 1;
}
