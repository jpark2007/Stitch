/**
 * Draws one frame of the threads screen on a 2D canvas and returns the
 * screen geometry the UI needs for hit-testing and for placing DOM labels.
 *
 * Level of detail by zoom:
 *  - far:   threads as plain yarn, knots, decade labels
 *  - mid:   + ply twist, needle holes, yearly ticks
 *  - close: + sheen, loose fibres, moment titles (placed by the UI)
 */

import { toX, type Camera } from "./camera";
import { relax, strandY, type Knot, type Strand, type ThreadsModel } from "./model";
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
  const top = compact ? 132 : 150;
  const axisY = height - (compact ? 64 : 70);
  const avail = axisY - 48 - top;
  const gap = lanes > 1 ? Math.min(compact ? 72 : 82, avail / (lanes - 1)) : 0;
  // Centre the lane block in the space between header and axis.
  const block = gap * (lanes - 1);
  const top2 = top + Math.max(0, (avail - block) / 2);
  return { width, height, top: top2, gap, axisY };
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
  lod: number;
}

export function sigmaFor(s: number): number {
  return Math.min(6, Math.max(0.9, 78 / s));
}

export function threadWidth(s: number): number {
  return Math.min(4.6, Math.max(2.5, 2.2 + s * 0.012));
}

export function knotRadius(s: number, people: number): number {
  const base = Math.min(12, Math.max(5.5, 4.2 + s * 0.045));
  return people > 1 ? base * (1 + Math.min(0.45, (people - 2) * 0.12)) : base * 0.78;
}

function lodFor(s: number): number {
  if (s >= 60) return 2;
  if (s >= 14) return 1;
  return 0;
}

const scratch: Float32Array[] = [];

export function drawFrame(ctx: CanvasRenderingContext2D, model: ThreadsModel, st: FrameState): FrameGeometry {
  const { cam, layout } = st;
  const { width: W, height: H } = layout;
  const s = cam.s;
  const lod = lodFor(s);
  const sigma = sigmaFor(s);
  relax(model, sigma);

  ctx.clearRect(0, 0, W, H);
  const laneY = (l: number) => layout.top + l * layout.gap;
  const X = (t: number) => toX(t, cam, W);
  const tLeft = cam.cx - W / 2 / s;
  const tRight = cam.cx + W / 2 / s;

  drawAxis(ctx, st, tLeft, tRight, model);

  /* ---------------- threads ---------------- */
  const w0 = threadWidth(s);
  const dt = 3 / s; // sample every ~3px
  // Running stitch: period in years is a power of two so stitches only
  // ever split or merge while zooming, never slide.
  const periodPx = 24;
  const period = Math.pow(2, Math.ceil(Math.log2(periodPx / s)));
  const gapFrac = 0.16;

  const geom: FrameGeometry = { strands: [], strandLens: [], knots: [], lod };

  // Faded strands first so highlighted ones sit on top.
  const order = model.strands.map((_, i) => i).sort((a, b) => st.dim[b] - st.dim[a]);
  const knotT = model.knots.map((k) => k.t);

  for (const i of order) {
    const strand = model.strands[i];
    const ink = yarnInk(strand.person.color, st.dim[i]);
    const width = w0 + st.lift[i] * 1.1;
    const t0 = Math.max(strand.start.t, tLeft - 2 / s * 20);
    const t1 = Math.min(model.tNow, tRight + 40 / s, st.reveal);
    if (t1 <= t0) {
      geom.strands[i] = new Float32Array(0);
      geom.strandLens[i] = 0;
      continue;
    }
    const nSamples = Math.ceil((t1 - t0) / dt) + 1;
    let buf = scratch[i];
    if (!buf || buf.length < nSamples * 2) buf = scratch[i] = new Float32Array(nSamples * 2 + 64);
    for (let k = 0; k < nSamples; k++) {
      const t = Math.min(t1, t0 + k * dt);
      buf[k * 2] = X(t);
      buf[k * 2 + 1] = laneY(strandY(strand, t, sigma));
    }
    geom.strands[i] = buf.subarray(0, nSamples * 2);
    geom.strandLens[i] = nSamples;

    // Cut into stitches.
    const runs: Polyline[] = [];
    const holes: number[] = [];
    const knotClear = knotRadius(s, 2) * 1.4 / s; // years either side of a knot left unbroken
    let run: Polyline = [];
    for (let k = 0; k < nSamples; k++) {
      const t = t0 + k * dt;
      const ph = ((t / period) % 1 + 1) % 1;
      const nearKnot = lod > 0 && nearAny(knotT, t, knotClear);
      const inGap = lod > 0 && !nearKnot && ph > 1 - gapFrac;
      if (inGap) {
        if (run.length >= 4) {
          runs.push(run);
          holes.push(run[run.length - 2] + 0, run[run.length - 1]);
        }
        run = [];
      } else {
        if (run.length === 0 && runs.length > 0) holes.push(buf[k * 2], buf[k * 2 + 1]);
        run.push(buf[k * 2], buf[k * 2 + 1]);
      }
    }
    if (run.length >= 4) runs.push(run);

    if (lod > 0) drawHoles(ctx, holes, Math.max(0.9, width * 0.32), ink);
    // Where the thread comes up through the paper at its start.
    drawHoles(ctx, [buf[0], buf[1]], Math.max(1.1, width * 0.38), ink);
    drawYarn(ctx, runs, ink, width, lod);
    if (lod >= 2) drawFibres(ctx, runs, ink.body, width, hashSeed(strand.person.id));
  }

  /* ---------------- knots ---------------- */
  const knotOrder = model.knots.map((_, i) => i);
  const involvement = model.knots.map((k) => knotInvolvement(k, model.strands, st.dim));
  knotOrder.sort((a, b) => involvement[a] - involvement[b] || (a === st.hoverKnot ? 1 : b === st.hoverKnot ? -1 : 0));
  for (const ki of knotOrder) {
    const k = model.knots[ki];
    const x = X(k.t);
    const y = laneY(k.y);
    const r0 = knotRadius(s, k.people.length);
    const appear = Math.min(1, Math.max(0, (st.reveal - k.t) * 1.6));
    const pop = appear < 1 ? easeBack(appear) : 1;
    const r = r0 * pop * (1 + st.swell[ki] * 0.22);
    const visible = x > -r0 * 3 && x < W + r0 * 3 && appear > 0;
    geom.knots[ki] = { index: ki, x, y, r: r0 * (1 + st.swell[ki] * 0.22), visible };
    if (!visible || r < 0.5) continue;
    const fade = 1 - involvement[ki];
    const width = Math.max(1.6, w0 * 0.82) + (1 - fade) * 0.4;
    const seed = hashSeed(k.moment.id);
    if (k.people.length === 1) {
      const idx = model.strands.findIndex((q) => q.person.id === k.people[0].id);
      drawFrenchKnot(ctx, x, y, r, yarnInk(k.people[0].color, st.dim[idx] ?? 0), width, seed, Math.max(1, lod), 1 - fade * 0.7);
    } else {
      const inks = k.people.map((p) => {
        const idx = model.strands.findIndex((q) => q.person.id === p.id);
        return yarnInk(p.color, Math.max(st.dim[idx] ?? 0, fade * 0.85));
      });
      drawTiedKnot(ctx, x, y, r, inks, width, seed, Math.max(1, lod), 1 - fade * 0.7);
    }
  }

  /* ---------------- names ---------------- */
  drawNames(ctx, model, st, geom, w0);

  return geom;
}

/** How "lit" a knot is: 0 when any of its people is fully lit. */
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
    for (let i = 4; i < r.length - 4; i += 14) {
      n = (n * 1103515245 + 12345) & 0x7fffffff;
      const u = n / 0x7fffffff;
      if (u > 0.45) continue;
      const dx = r[i + 2] - r[i - 2];
      const dy = r[i + 3] - r[i - 1];
      const len = Math.hypot(dx, dy) || 1;
      const side = u < 0.22 ? 1 : -1;
      const nx = (-dy / len) * side;
      const ny = (dx / len) * side;
      const x = r[i] + nx * width * 0.45;
      const y = r[i + 1] + ny * width * 0.45;
      const L = width * (0.6 + u * 1.6);
      ctx.moveTo(x, y);
      ctx.quadraticCurveTo(x + nx * L * 0.6 + (dx / len) * L * 0.5, y + ny * L * 0.6 + (dy / len) * L * 0.5, x + nx * L + (dx / len) * L * 0.2, y + ny * L + (dy / len) * L * 0.2);
    }
  }
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = 0.6;
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
  const major = STEPS.find((d) => d * s >= 64 && d % minor === 0) ?? 50;
  const y = layout.axisY;
  const lo = Math.max(Math.ceil(tLeft / minor) * minor, Math.ceil(model.tMin / minor) * minor);
  const hi = Math.min(tRight, model.tMax);

  // Faint pencil guides at major years, behind everything.
  ctx.beginPath();
  for (let t = Math.ceil(lo / major) * major; t <= hi; t += major) {
    const x = Math.round(toX(t, cam, W)) + 0.5;
    ctx.moveTo(x, layout.top - 40);
    ctx.lineTo(x, y - 14);
  }
  ctx.setLineDash([2, 6]);
  ctx.strokeStyle = "rgba(47,38,32,0.075)";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.setLineDash([]);

  // Ticks.
  ctx.beginPath();
  for (let t = lo; t <= hi; t += minor) {
    const x = Math.round(toX(t, cam, W)) + 0.5;
    const isMajor = t % major === 0;
    ctx.moveTo(x, y - (isMajor ? 9 : 4));
    ctx.lineTo(x, y);
  }
  ctx.strokeStyle = "rgba(47,38,32,0.28)";
  ctx.lineWidth = 1;
  ctx.stroke();

  // A stitched baseline.
  ctx.beginPath();
  ctx.moveTo(Math.max(0, toX(model.tMin, cam, W)), y + 0.5);
  ctx.lineTo(Math.min(W, toX(model.tMax, cam, W)), y + 0.5);
  ctx.setLineDash([5, 4]);
  ctx.strokeStyle = "rgba(47,38,32,0.2)";
  ctx.stroke();
  ctx.setLineDash([]);

  // Labels. With single-year spacing the label sits in the middle of its year.
  ctx.fillStyle = "rgba(92,78,67,0.9)";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const decadeFont = `600 13px ${st.fonts.sans}`;
  const yearFont = `500 12px ${st.fonts.sans}`;
  for (let t = Math.ceil(lo / major) * major; t <= hi; t += major) {
    const mid = major === 1 && s > 70 ? 0.5 : 0;
    const x = toX(t + mid, cam, W);
    const isDecade = t % 10 === 0;
    ctx.font = isDecade ? decadeFont : yearFont;
    ctx.fillStyle = isDecade ? "rgba(92,78,67,0.92)" : "rgba(143,129,115,0.95)";
    const label = isDecade && major >= 10 ? `${t}s` : String(t);
    ctx.fillText(label, x, y + 10);
  }
}

/* ---------------- names ---------------- */

function drawNames(ctx: CanvasRenderingContext2D, model: ThreadsModel, st: FrameState, geom: FrameGeometry, w0: number) {
  const { layout } = st;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  const size = layout.width < 640 ? 19 : 21;
  ctx.font = `600 ${size}px ${st.fonts.hand}`;
  for (let i = 0; i < model.strands.length; i++) {
    const pts = geom.strands[i];
    const n = geom.strandLens[i];
    if (!pts || n < 2) continue;
    const strand = model.strands[i];
    // Sit just after where the thread starts, or ride the left margin.
    const startX = pts[0];
    const x = Math.max(startX + 12, 14);
    // y of the thread at x.
    let k = 0;
    while (k < n - 1 && pts[k * 2] < x + 18) k++;
    const y = pts[k * 2 + 1] - w0 - 7;
    const ink = yarnInk(strand.person.color, st.dim[i]);
    const riding = startX + 12 < 14;
    ctx.globalAlpha = riding ? 0.78 : 1;
    ctx.fillStyle = ink.body;
    ctx.fillText(strand.person.name, x, y);
  }
  ctx.globalAlpha = 1;
}
