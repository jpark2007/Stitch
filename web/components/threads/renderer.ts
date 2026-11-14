/**
 * Draws one frame of the threads screen on a 2D canvas and returns the
 * screen geometry the UI needs for hit-testing and for placing DOM labels.
 *
 * Level of detail by zoom:
 *  - far:   laid yarn (continuous), knots, decade labels
 *  - mid:   + ply twist; the yarn opens into a running stitch with needle holes
 *  - close: + sheen, loose fibres, moment titles (placed by the UI)
 */

import { toT, toX, type Camera } from "./camera";
import { relax, strandAt, type Knot, type Shape, type Strand, type ThreadsModel } from "./model";
import { NAME_GUTTER, NAME_MIN_PX, placeNames, type NameBox, type NameRequest, type NameSpot } from "./names";
import { paperAt } from "./titles";
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
  /** Followed strand, or -1: its name is always written. */
  highlight?: number;
  /** Per strand: 1 when its name was written last frame (names.ts keeps it, so names do not flicker). */
  nameShown?: Uint8Array;
  /**
   * Per strand: the year under its name's left edge last frame, NaN when not
   * written. Kept in years so a pan does not move it: the name holds its
   * place on its thread instead of hopping between spots. Updated in place.
   */
  nameAt?: Float64Array;
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
  names: NameBox[];
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
    const t1 = Math.min(strand.end, tRight + 30 / s, st.reveal);
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
    // ...and where it goes back down, today; a life that has ended is
    // knotted off where it ends, as it was where it began.
    if (t1 >= strand.end - 1e-6) {
      const e = (nSamples - 1) * 2;
      if (strand.tiedOff) starts.push({ i, x: buf[e], y: buf[e + 1], ink, width });
      else drawHoles(ctx, [buf[e] + width * 0.6, buf[e + 1]], Math.max(1.3, width * 0.45), ink);
    }
    drawYarn(ctx, runs, ink, width, lod);
    if (lod >= 2) drawFibres(ctx, runs, ink.body, width, seed);
  }

  /* ---------------- start (and end) knots ---------------- */
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

/**
 * Does any thread but `self` pass through the box? Samples are evenly spaced
 * in x; a steep stretch can step right over the box between two samples, so
 * each segment is tested, not just its ends, cut to the box's sides: an
 * untrimmed segment reaches up to a sample past the box, and whether it does
 * changes as the view pans across the sample grid, so a name at the edge of
 * a converging thread would blink on and off.
 */
function crossedBy(g: FrameGeometry, self: number, x0: number, x1: number, y0: number, y1: number): boolean {
  return crossings(g, self, x0, x1, y0, y1, 1) > 0;
}

/** How many threads but `self` pass through the box (as crossedBy), counting up to `limit`. */
function crossings(g: FrameGeometry, self: number, x0: number, x1: number, y0: number, y1: number, limit: number): number {
  let count = 0;
  for (let j = 0; j < g.strands.length && count < limit; j++) {
    if (j === self) continue;
    const pts = g.strands[j];
    const n = g.strandLens[j];
    if (!pts || n < 2) continue;
    const step = pts[2] - pts[0] || 1;
    const k0 = Math.max(1, Math.floor((x0 - pts[0]) / step));
    const k1 = Math.min(n - 1, Math.ceil((x1 - pts[0]) / step));
    for (let k = k0; k <= k1; k++) {
      const xa = pts[k * 2 - 2];
      const xb = pts[k * 2];
      const sa = Math.max(x0, xa);
      const sb = Math.min(x1, xb);
      if (sa > sb) continue;
      const ya = pts[k * 2 - 1];
      const dy = (pts[k * 2 + 1] - ya) / (xb - xa || 1);
      const p = ya + (sa - xa) * dy;
      const y = ya + (sb - xa) * dy;
      if (Math.min(y, p) < y1 && Math.max(y, p) > y0) {
        count++;
        break;
      }
    }
  }
  return count;
}

/** True when a name `w` wide at (x, baseline yb) keeps a few px clear of every knot in view. */
function clearOfKnots(g: FrameGeometry, x: number, w: number, yb: number, size: number): boolean {
  for (const h of g.knots) {
    if (h && h.visible && h.x + h.r + 6 > x - 2 && h.x - h.r - 6 < x + w + 2 && h.y + h.r + 6 > yb - size && h.y - h.r - 6 < yb + 3) return false;
  }
  return true;
}

/**
 * A thread that never settles in view: the left edge x (from x0 to x1) where
 * a name `w` wide spans the flattest stretch of strand `self`, and the
 * baseline `drop` off that stretch's highest point, with the name's box clear
 * of knots. Null when there is no such stretch.
 */
function swingSpot(
  g: FrameGeometry,
  self: number,
  w: number,
  size: number,
  x0: number,
  x1: number,
  drop: number,
): { x: number; y: number } | null {
  const pts = g.strands[self];
  const n = g.strandLens[self];
  if (x1 < x0) return null;
  let bestX = NaN;
  let bestY = 0;
  let bestSpan = Infinity;
  for (let k = 0; k < n; k++) {
    const x = pts[k * 2];
    if (x < x0) continue;
    if (x > x1) break;
    let lo = Infinity;
    let hi = -Infinity;
    for (let j = k; j < n && pts[j * 2] <= x + w; j++) {
      const y = pts[j * 2 + 1];
      if (y < lo) lo = y;
      if (y > hi) hi = y;
    }
    if (!(hi - lo < bestSpan)) continue;
    const yb = lo + drop;
    if (!clearOfKnots(g, x, w, yb, size)) continue;
    bestSpan = hi - lo;
    bestX = x;
    bestY = yb;
  }
  return Number.isFinite(bestX) ? { x: bestX, y: bestY } : null;
}

/** Name size (px) for this layout: big families pack the lanes closer, names follow (never below legible). */
export function nameFontPx(layout: Layout): number {
  const full = layout.width < 640 ? 19 : 21;
  return layout.gap > 0 ? Math.round(Math.max(NAME_MIN_PX, Math.min(full, layout.gap * 0.6))) : full;
}

/**
 * Lanes closer than this (px) leave no room for a name above its thread
 * without crossing the thread above, so names are written on the thread
 * itself instead, the yarn parted under the letters.
 */
export const INLINE_NAMES_GAP = 26;

/** True when this layout writes names on their threads rather than above them. */
export function namesInline(layout: Layout): boolean {
  return layout.gap > 0 && layout.gap < INLINE_NAMES_GAP;
}

/** Spots offered per thread walking forward from its start: the first, then staggered further along. */
const FORWARD_SPOTS = 3;

/**
 * Each thread is signed with its person's name, written just above it
 * where it first settles on its own lane (past the knot it may be born
 * into). When that point scrolls off the left, the name rides the margin.
 *
 * Here each thread is offered a few clean spots (over a level stretch of
 * its own thread, clear of knots and other threads); names.ts picks one per
 * thread so no two names touch and all stay inside the gutter, dropping the
 * least important when a crowded family has no room for everyone. In tight
 * lanes (namesInline) the name is written on its thread instead of above.
 */
function drawNames(ctx: CanvasRenderingContext2D, model: ThreadsModel, st: FrameState, geom: FrameGeometry, w0: number) {
  const { layout } = st;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  const size = nameFontPx(layout);
  ctx.font = `600 ${size}px ${st.fonts.hand}`;
  const inline = namesInline(layout);
  // Baseline below the thread's y: above it, or on it with the x-height centred.
  const drop = inline ? Math.round(size * 0.28) : -(w0 + 8);
  const level = inline ? 2.5 : 6;
  const margin = NAME_GUTTER;
  const right = layout.width - margin;
  const requests: NameRequest[] = [];
  const labels: string[] = [];
  for (let i = 0; i < model.strands.length; i++) {
    const pts = geom.strands[i];
    const n = geom.strandLens[i];
    if (!pts || n < 2) continue;
    const strand = model.strands[i];
    const lanePx = layout.top + strand.lane * layout.gap;
    const label = strand.person.label ?? strand.person.name;
    labels[i] = label;
    const w = ctx.measureText(label).width;
    const startX = pts[0];
    const endX = pts[(n - 1) * 2];
    // Does a label starting at x sit over level thread (all of it over the
    // thread, not past its end), clear of knots?
    const settled = (k: number, x: number) => {
      if (x < margin || x + w > right || x + w > endX) return false;
      if (Math.abs(pts[k * 2 + 1] - lanePx) > 2) return false;
      for (let j = k; j < n && pts[j * 2] < x + w; j += 4) {
        if (Math.abs(pts[j * 2 + 1] - lanePx) > level) return false;
      }
      return !geom.knots.some(
        (h) => h && h.visible && h.x + h.r + 8 > x && h.x - h.r - 8 < x + w + 6 && Math.abs(h.y - lanePx) < h.r + 22,
      );
    };
    // How many other threads run through a name written at (x, baseline y).
    const crossing = (x: number, yb: number) => crossings(geom, i, x - 2, x + w + 2, yb - size, yb + 3, 9);
    // Settled, and no other thread runs through the name: a clean spot.
    const fits = (k: number, x: number) => settled(k, x) && !crossedBy(geom, i, x - 2, x + w + 2, lanePx + drop - size, lanePx + drop + 3);
    const spots: NameSpot[] = [];
    // Settled spots another thread runs through, with how many: offered (as
    // a `cost`, the paper halo keeping the name legible) only when the
    // thread has no clean spot, so a small family never loses a name to a
    // neighbour merely passing by.
    let crossedFwd: NameSpot | null = null;
    let crossedBack: NameSpot | null = null;
    let crossedEnd: NameSpot | null = null;
    const baseline = (k: number) => pts[k * 2 + 1] + drop;
    // Where the name was last frame, moved with the pan, so names.ts can keep
    // it there (or nudge it along) instead of hopping between spots.
    const heldT = st.nameAt ? st.nameAt[i] : NaN;
    const held = Number.isFinite(heldT) ? toX(heldT, st.cam, layout.width) : NaN;
    // Walking forward from the start: the first clean spot, then a couple
    // more a name's width further on, for when a neighbour has the first.
    // Clear of the knot the thread starts with (more when the yarn is parted under the name).
    const lead = startX >= margin ? (inline ? 14 : 6) : 0;
    let from = -Infinity;
    for (let k = 0; k < n && spots.length < FORWARD_SPOTS; k++) {
      const px = Math.max(margin, pts[k * 2] + lead);
      if (px + w > right) break;
      if (px < from || !settled(k, px)) continue;
      const c = crossing(px, baseline(k));
      if (!c) {
        spots.push({ x: px, y: baseline(k) });
        from = px + w + 18;
      } else crossedFwd ??= { x: px, y: baseline(k), cost: c };
    }
    // Late in the view (a late thread near the right edge): sign the last
    // level stretch too, right-aligned.
    for (let k = n - 1; k >= 0; k -= 2) {
      const px = Math.min(pts[k * 2], right) - w;
      let k0 = k;
      while (k0 > 0 && pts[k0 * 2] > px) k0--;
      // It may reach back over where the thread begins (not on a parted
      // thread, though), but not wholly before it, nor back over the spots
      // found above.
      if (px < (inline ? startX + lead : startX + 12 - w) || px < from - w - 18) break;
      if (!settled(k0, px)) continue;
      const c = crossing(px, baseline(k0));
      if (!c) {
        spots.push({ x: px, y: baseline(k0) });
        break;
      }
      crossedBack ??= { x: px, y: baseline(k0), cost: c };
    }
    // Written on the thread, a name can also lead in to it: just before the
    // knot it starts with (a thread born late in the view, near the edge).
    if (inline && startX >= margin) {
      // Far enough out that the parted-yarn band stops short of the knot.
      const lx = startX - 13 - w;
      const ly = pts[1] + drop;
      if (lx >= margin && !crossedBy(geom, i, lx - 6, startX - 6, ly - size, ly + 3)) spots.push({ x: lx, y: ly });
    }
    // Nowhere clean (a thread still swinging as it ends): sign its end, clear of knots.
    if (!spots.length && endX <= right && endX - w >= margin && clearOfKnots(geom, endX - w, w, baseline(n - 1), size)) {
      const ey = baseline(n - 1);
      const c = crossing(endX - w, ey);
      if (!c) spots.push({ x: endX - w, y: ey });
      else crossedEnd = { x: endX - w, y: ey, cost: c };
    }
    // Still nowhere clean: the crossed spots, fewest threads through the name first.
    if (!spots.length) {
      for (const sp of [crossedFwd, crossedBack, crossedEnd]) if (sp) spots.push(sp);
      spots.sort((a, b) => (a.cost ?? 0) - (b.cost ?? 0));
    }
    // Nowhere settled at all (a thread swinging the whole way across the
    // view): over its flattest stretch inside the gutter, the name written
    // just above the stretch's highest point, so its own thread stays clear
    // of the letters. Not in tight lanes, where it would float into the next.
    if (!spots.length && !inline) {
      const sp = swingSpot(geom, i, w, size, Math.max(margin, startX + lead), Math.min(right, endX) - w, drop);
      if (sp) spots.push({ x: sp.x, y: sp.y, cost: 10 + crossing(sp.x, sp.y) });
    }
    const pinned = i === st.highlight;
    if (pinned) {
      // The followed name is always written: failing a clean spot, where its
      // thread first shows inside the gutter.
      let k = 0;
      while (k < n - 1 && pts[k * 2] < margin) k++;
      spots.push({ x: Math.max(margin, pts[k * 2] + lead), y: baseline(k), forced: true });
    }
    // A clean spot at any x on the thread, past the knot it starts with, as the forward spots are.
    const step = pts[2] - pts[0] || 1;
    const fit = Number.isFinite(held)
      ? (x: number) => {
          if (x < startX + lead) return NaN;
          const k = Math.min(n - 1, Math.floor((x - startX) / step));
          return fits(k, x) ? baseline(k) : NaN;
        }
      : undefined;
    // With only crossed spots: the same, other threads allowed through the name.
    const fitCrossed =
      Number.isFinite(held) && spots[0]?.cost
        ? (x: number) => {
            if (x < startX + lead) return NaN;
            const k = Math.min(n - 1, Math.floor((x - startX) / step));
            return settled(k, x) ? baseline(k) : NaN;
          }
        : undefined;
    if (!spots.length && !fit) continue;
    // Lit threads first, then the hovered one, then whoever is in the most moments.
    const rank = (st.dim[i] < 0.5 ? 1000 : 0) + st.lift[i] * 200 + strand.momentIds.length;
    requests.push({ strand: i, width: w, spots, rank, pinned, held, fit, fitCrossed });
  }

  const placed = placeNames(requests, { width: layout.width, size, shown: st.nameShown });
  if (st.nameAt) {
    st.nameAt.fill(NaN);
    for (const p of placed) st.nameAt[p.strand] = toT(p.x, st.cam, layout.width);
  }
  ctx.lineJoin = "round";
  ctx.lineWidth = 4;
  for (const p of placed) {
    const i = p.strand;
    const riding = (geom.strands[i]?.[0] ?? 0) < margin;
    geom.names.push(p);
    ctx.globalAlpha = riding ? 0.85 : 1;
    const paper = paperAt(p.y - size * 0.35);
    if (inline) {
      // Part the yarn under the name, as if it were written in a gap of the stitching.
      ctx.fillStyle = paper;
      ctx.fillRect(p.x0 - 5, p.y - drop - 4.5, p.x1 - p.x0 + 10, 9);
    }
    // A paper halo, so a forced name (or a thread passing close) stays legible.
    ctx.strokeStyle = paper;
    ctx.strokeText(labels[i], p.x, p.y);
    ctx.fillStyle = yarnInk(model.strands[i].person.color, st.dim[i]).label;
    ctx.fillText(labels[i], p.x, p.y);
  }
  ctx.globalAlpha = 1;
}
