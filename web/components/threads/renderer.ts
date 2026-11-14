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
import { drawFrenchKnot, drawHoles, drawTiedKnot, drawYarn, hashSeed, yarnInk, type Polyline } from "./yarn";

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
  fonts: { hand: string; sans: string };
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
  const base = Math.min(12.5, Math.max(7, 5.6 + s * 0.04));
  return people > 1 ? base * (1 + Math.min(0.4, (people - 2) * 0.13)) : base * 0.72;
}

function lodFor(s: number): number {
  if (s >= 60) return 2;
  if (s >= 12) return 1;
  return 0;
}

/** How open the running stitch is: 0 (laid yarn) far out, ~0.14 close in. */
function stitchGap(s: number): number {
  const u = Math.min(1, Math.max(0, (s - 20) / 50));
  return u * u * (3 - 2 * u) * 0.14;
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
  const gapFrac = stitchGap(s);
  const knotT = model.knots.map((k) => k.t);
  const knotClear = (knotRadius(s, 3) * 1.3) / s; // years either side of a knot left unbroken

  const geom: FrameGeometry = { strands: [], strandLens: [], knots: [], names: [], lod };

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

    // Cut into stitches (each slightly irregular, as if sewn by hand).
    const runs: Polyline[] = [];
    const holes: number[] = [];
    let run: Polyline = [];
    const holeOut = width * 0.75;
    const pushHole = (k: number, dir: number) => {
      // Just past the stitch end, into the gap: where the needle went in.
      const ka = Math.max(0, Math.min(nSamples - 1, k - dir));
      const dx = buf[k * 2] - buf[ka * 2];
      const dy = buf[k * 2 + 1] - buf[ka * 2 + 1];
      const len = Math.hypot(dx, dy) || 1;
      holes.push(buf[k * 2] + (dx / len) * holeOut, buf[k * 2 + 1] + (dy / len) * holeOut);
    };
    for (let k = 0; k < nSamples; k++) {
      let inGap = false;
      if (gapFrac > 0.004) {
        const p = phase[k] / period;
        const cell = Math.floor(p);
        const g = gapFrac * (0.75 + rand(cell, seed) * 0.5);
        inGap = p - cell > 1 - g && !nearAny(knotT, Math.min(t1, t0 + k * dt), knotClear);
      }
      if (inGap) {
        if (run.length >= 4) {
          runs.push(run);
          pushHole(k - 1, 1);
        }
        run = [];
      } else {
        if (run.length === 0 && runs.length > 0) pushHole(k, -1);
        run.push(buf[k * 2], buf[k * 2 + 1]);
      }
    }
    if (run.length >= 4) runs.push(run);

    if (holes.length) drawHoles(ctx, holes, Math.max(0.8, width * 0.3) * Math.min(1, gapFrac * 9), ink);
    // Where the thread comes up through the paper at its start.
    if (strand.start.t >= tLeft - 1 && strand.start.t <= st.reveal) drawHoles(ctx, [buf[0], buf[1]], Math.max(1.3, width * 0.45), ink);
    drawYarn(ctx, runs, ink, width, lod);
    if (lod >= 2) drawFibres(ctx, runs, ink.body, width, seed);
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
    const appear = Math.min(1, Math.max(0, (st.reveal - k.t) * 1.4));
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
      drawFrenchKnot(ctx, x, y, r, yarnInk(k.people[0].color, st.dim[idx] ?? 0), width * 1.05, seed, Math.max(1, lod), 1 - fade * 0.75);
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

function drawAxis(ctx: CanvasRenderingContext2D, st: FrameState, tLeft: number, tRight: number, model: ThreadsModel) {
  const { cam, layout } = st;
  const s = cam.s;
  const W = layout.width;
  const minor = STEPS.find((d) => d * s >= 9) ?? 50;
  const major = STEPS.find((d) => d * s >= (W < 640 ? 56 : 62) && d % minor === 0) ?? 50;
  const y = layout.axisY;
  const lo = Math.max(Math.ceil(tLeft / minor) * minor, Math.ceil(model.tMin / minor) * minor);
  const hi = Math.min(tRight, model.tMax);
  const xL = Math.max(0, toX(model.tMin, cam, W));
  const xR = Math.min(W, toX(model.tMax, cam, W));

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
  ctx.setLineDash([]);

  // Ticks.
  ctx.beginPath();
  for (let t = lo; t <= hi; t += minor) {
    const x = Math.round(toX(t, cam, W)) + 0.5;
    const isMajor = t % major === 0;
    ctx.moveTo(x, y - (isMajor ? 8 : 4));
    ctx.lineTo(x, y);
  }
  ctx.strokeStyle = "rgba(47,38,32,0.26)";
  ctx.lineWidth = 1;
  ctx.stroke();

  // A stitched baseline.
  ctx.beginPath();
  ctx.moveTo(xL, y + 0.5);
  ctx.lineTo(xR, y + 0.5);
  ctx.setLineDash([6, 4]);
  ctx.strokeStyle = "rgba(47,38,32,0.22)";
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.setLineDash([]);

  // Labels. At single-year spacing the label sits in the middle of its year.
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const decadeFont = `700 12.5px ${st.fonts.sans}`;
  const yearFont = `600 12px ${st.fonts.sans}`;
  for (let t = Math.ceil(lo / major) * major; t <= hi; t += major) {
    const mid = major === 1 && s > 70 ? 0.5 : 0;
    const x = toX(t + mid, cam, W);
    const isDecade = t % 10 === 0;
    ctx.font = isDecade ? decadeFont : yearFont;
    ctx.fillStyle = isDecade ? "rgba(92,78,67,0.95)" : "rgba(143,129,115,0.95)";
    const label = isDecade && major >= 10 ? `${t}s` : String(t);
    ctx.fillText(label, x, y + 11);
  }
}

/* ---------------- names ---------------- */

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
    // First sample that sits on the lane and is clear of knots.
    let k = 0;
    const startX = pts[0];
    for (; k < n; k++) {
      const x = pts[k * 2];
      if (x < margin) continue;
      if (Math.abs(pts[k * 2 + 1] - lanePx) > 2) continue;
      if (geom.knots.some((h) => h && h.visible && Math.abs(h.x - x) < h.r + 14 && Math.abs(h.y - lanePx) < h.r + 20)) continue;
      break;
    }
    if (k >= n) continue;
    const x = Math.max(margin, pts[k * 2] + (startX >= margin ? 6 : 0));
    const y = pts[k * 2 + 1] - w0 - 8;
    const riding = startX < margin;
    const w = ctx.measureText(strand.person.name).width;
    geom.names.push({ x0: x, y0: y - size, x1: x + w, y1: y + 4 });
    ctx.globalAlpha = riding ? 0.85 : 1;
    ctx.fillStyle = yarnInk(strand.person.color, st.dim[i]).label;
    ctx.fillText(strand.person.name, x, y);
  }
  ctx.globalAlpha = 1;
}
