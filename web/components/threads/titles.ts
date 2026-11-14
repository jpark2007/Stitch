/**
 * Where moment titles go, frame by frame: pure, no DOM.
 *
 * Titles fade in as you zoom towards single years; the followed person's
 * and the hovered knot's show at any zoom. Each is placed greedily above or
 * below its knot, clear of other titles, knots, names and the header, on
 * the spot the fewest threads run through, and it keeps last frame's spot
 * unless another is clearly better, so titles do not hop while panning.
 */

import type { ThreadsModel } from "./model";
import type { FrameGeometry } from "./renderer";

export const TITLE_FONT_PX = 20;
/** Phones set titles a size smaller, so more of them find room. */
export const TITLE_FONT_PX_COMPACT = 17;

/** Title size (px) for a view this wide; matches the CSS breakpoint in threads.css. */
export function titleFontPx(width: number): number {
  return width < 640 ? TITLE_FONT_PX_COMPACT : TITLE_FONT_PX;
}

export interface TitleBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  knot: number;
}

export interface TitlePlacement {
  /** Offset of the title's top-left from its knot centre (px). */
  dx: number;
  dy: number;
  /** 0..1 opacity. */
  alpha: number;
}

export interface TitleInput {
  geom: FrameGeometry;
  /** Per strand 0..1 fade. */
  dim: number[];
  /** Followed strand, or -1. */
  highlight: number;
  hoverKnot: number;
  /** Pixels per year. */
  s: number;
  width: number;
  /** Measured title widths (px), per knot. */
  widths: number[];
  /** Which spot each title used last frame; updated in place. */
  spots: Map<number, number>;
}

export interface TitleLayout {
  boxes: TitleBox[];
  shown: Map<number, TitlePlacement>;
}

/** 0 below `a`, 1 above `b`, smooth in between. */
export function ramp(a: number, b: number, x: number): number {
  const u = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return u * u * (3 - 2 * u);
}

/**
 * The paper colour at screen height y: the page is lit from the top and
 * darkens towards the bottom (globals.css), so a title halo in plain
 * --paper would show as a pale patch low on the screen. Measured from
 * screenshots; good to a few levels either way.
 */
export function paperAt(y: number): string {
  const k = Math.min(1, Math.max(0, y / 620));
  const c = (a: number, b: number) => Math.round(a + (b - a) * k);
  return `rgb(${c(244, 227)},${c(237, 216)},${c(224, 201)})`;
}

/**
 * How much thread runs through a title box: samples inside it, faded
 * threads counting for less. Samples are evenly spaced in x, so only the
 * slice under the box is checked.
 */
function threadsThrough(g: FrameGeometry, dim: number[], box: { x0: number; y0: number; x1: number; y1: number }): number {
  let score = 0;
  for (let i = 0; i < g.strands.length; i++) {
    const pts = g.strands[i];
    const n = g.strandLens[i];
    if (!pts || n < 2) continue;
    const step = pts[2] - pts[0] || 1;
    const k0 = Math.max(0, Math.floor((box.x0 - pts[0]) / step));
    const k1 = Math.min(n - 1, Math.ceil((box.x1 - pts[0]) / step));
    const weight = 1 - dim[i] * 0.9;
    for (let k = k0; k <= k1; k += 2) {
      const y = pts[k * 2 + 1];
      if (y > box.y0 - 3 && y < box.y1 + 3) score += weight;
    }
  }
  return score;
}

export function placeTitles(model: ThreadsModel, inp: TitleInput): TitleLayout {
  const { geom: g, dim, highlight, hoverKnot, s, width: W, widths, spots } = inp;
  const base = ramp(50, 80, s);
  const alpha = new Map<number, number>();
  const candidates: number[] = [];
  for (let i = 0; i < model.knots.length; i++) {
    const h = g.knots[i];
    if (!h || !h.visible) continue;
    const lit = model.knots[i].people.some((p) => {
      const idx = model.strands.findIndex((q) => q.person.id === p.id);
      return dim[idx] < 0.5;
    });
    const o = i === hoverKnot || (highlight >= 0 && lit) ? 1 : highlight < 0 ? base : 0;
    if (o > 0.02) {
      candidates.push(i);
      alpha.set(i, o);
    }
  }
  candidates.sort((a, b) => {
    if (a === hoverKnot) return -1;
    if (b === hoverKnot) return 1;
    return model.knots[b].people.length - model.knots[a].people.length || a - b;
  });

  const boxes: TitleBox[] = [];
  const shown = new Map<number, TitlePlacement>();
  const knotBoxes = g.knots.filter((k) => k && k.visible);
  const hTxt = titleFontPx(W) * 1.15;
  for (const i of candidates) {
    const h = g.knots[i];
    const w = widths[i];
    const up = -(h.r + 8 + hTxt);
    const down = h.r + 8;
    const tries = [
      { dy: up, dx: -w / 2 },
      { dy: down, dx: -w / 2 },
      { dy: up, dx: -w * 0.12 },
      { dy: up, dx: -w * 0.88 },
      { dy: down, dx: -w * 0.12 },
      { dy: down, dx: -w * 0.88 },
      { dy: up - hTxt * 0.9, dx: -w / 2 },
      { dy: down + hTxt * 0.9, dx: -w / 2 },
    ];
    let bestBox: TitleBox | null = null;
    let bestScore = Infinity;
    let bestIdx = -1;
    let prevBox: TitleBox | null = null;
    let prevScore = Infinity;
    const prevIdx = spots.get(i) ?? -1;
    for (let ti = 0; ti < tries.length; ti++) {
      const tr = tries[ti];
      const box: TitleBox = { x0: h.x + tr.dx, y0: h.y + tr.dy, x1: h.x + tr.dx + w, y1: h.y + tr.dy + hTxt, knot: i };
      if (box.x0 < 8 || box.x1 > W - 8) continue;
      const hitsTitle = boxes.some((b) => b.x0 < box.x1 + 10 && box.x0 < b.x1 + 10 && b.y0 < box.y1 + 2 && box.y0 < b.y1 + 2);
      const hitsKnot = knotBoxes.some(
        (k) => k.index !== i && k.x + k.r > box.x0 && k.x - k.r < box.x1 && k.y + k.r > box.y0 && k.y - k.r < box.y1,
      );
      const hitsName = g.names.some((b) => b.x0 < box.x1 + 6 && box.x0 < b.x1 + 6 && b.y0 < box.y1 && box.y0 < b.y1);
      const hitsHeader = box.y0 < (W < 640 ? 150 : 136) && box.x0 < 520;
      if (hitsTitle || hitsKnot || hitsName || hitsHeader) continue;
      // Centred spots first: each step down the list must earn its place.
      const score = threadsThrough(g, dim, box) + ti * 2;
      if (ti === prevIdx) {
        prevBox = box;
        prevScore = score;
      }
      if (score < bestScore - 0.5) {
        bestScore = score;
        bestBox = box;
        bestIdx = ti;
      }
    }
    if (prevBox && prevScore <= bestScore + 4) {
      bestBox = prevBox;
      bestIdx = prevIdx;
    }
    if (bestBox) {
      boxes.push(bestBox);
      shown.set(i, { ...tries[bestIdx], alpha: alpha.get(i) ?? 1 });
      spots.set(i, bestIdx);
    } else spots.delete(i);
  }
  return { boxes, shown };
}
