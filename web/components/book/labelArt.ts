/**
 * Small printed things on a moment's spread, drawn on <canvas> like the rest
 * of the page (pageArt.ts):
 *
 *   drawTellerLabels   one taped paper label per telling on the story page,
 *                      a tiny paper puppet of the teller and their name in
 *                      handwriting ("Hear Ana's version"); the playing one
 *                      is inked and stitched in their thread colour
 *   drawPullTabPrint   on the stage page: the slit a pull tab comes out of,
 *                      and its label pencilled beside it
 *   drawLeverFace      the pull tab itself: folded card with a pencil arrow
 *
 * Geometry is in page pixels (PAGE_PX_W × PAGE_PX_H), x from the left edge
 * of the canvas, y from the head. Browser-only.
 */

import { mix, shade, tint } from "@/components/tree/sketch";
import { ACCENT, INK, INK_FAINT, INK_SOFT, canvas, drawStitchLine, drawTape, rng, setSpacing, type Ctx, type PageFonts } from "./paperArt";
import type { BookPullTab, BookTeller } from "./types";

/** A label's place on its page, for hit testing (page px, unrotated box). */
export interface LabelRect {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/* ------------------------------------------------------------------------ */
/* The puppet portrait                                                       */
/* ------------------------------------------------------------------------ */

/** A capsule from p to q, `w` wide (the tree's paper puppet, components/tree/Puppet.tsx). */
function capsule(p: [number, number], q: [number, number], w: number): Path2D {
  const dx = q[0] - p[0];
  const dy = q[1] - p[1];
  const len = Math.hypot(dx, dy) || 1;
  const nx = (-dy / len) * (w / 2);
  const ny = (dx / len) * (w / 2);
  const r = w / 2;
  return new Path2D(
    `M${p[0] + nx} ${p[1] + ny} L${q[0] + nx} ${q[1] + ny} A${r} ${r} 0 0 0 ${q[0] - nx} ${q[1] - ny} ` +
      `L${p[0] - nx} ${p[1] - ny} A${r} ${r} 0 0 0 ${p[0] + nx} ${p[1] + ny} Z`,
  );
}

/**
 * The teller's split-pin paper puppet, as on the family tree, standing in a
 * box `h` tall whose top-left is (x, y). `wave` raises the near arm: telling.
 */
export function drawPuppetAvatar(ctx: Ctx, x: number, y: number, h: number, color: string, wave = false) {
  const s = h / 130;
  const torso = color;
  const arm = shade(color, 0.14);
  const leg = shade(color, 0.3);
  const head = mix(tint(color, 0.55), "#ead9bc", 0.45);
  const edge = tint(color, 0.5);
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(s, s);
  // Each paper layer casts a soft shadow on the one under it.
  ctx.shadowColor = "rgba(74,47,29,0.32)";
  ctx.shadowBlur = 1.6;
  ctx.shadowOffsetX = 0.4;
  ctx.shadowOffsetY = 1;
  ctx.lineWidth = 0.9;
  ctx.strokeStyle = edge;
  const part = (p: Path2D, fill: string) => {
    ctx.fillStyle = fill;
    ctx.fill(p);
    ctx.stroke(p);
  };
  part(capsule([44.5, 82], [43.5, 120], 10), leg);
  part(capsule([55.5, 82], [56.5, 120], 10), leg);
  const neck = new Path2D("M46 35 h8 v9 h-8 Z");
  ctx.fillStyle = head;
  ctx.fill(neck);
  part(new Path2D("M35.5 45 Q50 38.5 64.5 45 Q66.5 64 67 85 Q50 89 33 85 Q33.5 64 35.5 45 Z"), torso);
  part(capsule([37.5, 47.5], [29, 81], 8.6), arm);
  // Telling: the far arm lifts, as if mid-story.
  part(wave ? capsule([62.5, 47.5], [79, 24], 8.6) : capsule([62.5, 47.5], [71, 81], 8.6), arm);
  const face = new Path2D();
  face.arc(50, 25, 13.2, 0, Math.PI * 2);
  part(face, head);
  ctx.shadowColor = "transparent";
  // Brass split pins at the joints.
  ctx.fillStyle = "#d9b46a";
  ctx.strokeStyle = "#7a5a2e";
  ctx.lineWidth = 0.45;
  for (const [cx, cy] of [
    [37.5, 48],
    [62.5, 48],
    [45, 84],
    [55, 84],
  ]) {
    ctx.beginPath();
    ctx.arc(cx, cy, 1.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

/* ------------------------------------------------------------------------ */
/* Teller labels                                                             */
/* ------------------------------------------------------------------------ */

const LABEL_H = 150;
const LABEL_GAP = 40;
const LABEL_MAX_W = 345;

/** Where the labels go: up to two a row from `left`, `width` wide, starting at `y`. */
export function layoutTellerLabels(tellers: readonly BookTeller[], left: number, width: number, y: number): LabelRect[] {
  const perRow = tellers.length === 1 ? 1 : 2;
  const w = Math.min(LABEL_MAX_W, (width - LABEL_GAP * (perRow - 1)) / perRow);
  return tellers.map((t, i) => {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    return { id: t.id, x: left + col * (w + LABEL_GAP) + (row % 2) * 18, y: y + row * (LABEL_H + 28), w, h: LABEL_H };
  });
}

/** The height the labels for `count` tellers take. */
export function tellerLabelsHeight(count: number): number {
  const rows = Math.ceil(count / (count === 1 ? 1 : 2));
  return rows * LABEL_H + (rows - 1) * 28;
}

/**
 * A pencilled question over the labels, "Who's telling?", with a small
 * curved arrow down to them. Returns the height it takes.
 */
export function drawLabelsPrompt(ctx: Ctx, x: number, y: number, fonts: PageFonts, seed: number): number {
  ctx.save();
  ctx.fillStyle = "rgba(78,72,68,0.78)";
  ctx.font = `500 38px ${fonts.hand}`;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.translate(x, y + 34);
  ctx.rotate(-0.02);
  const text = "Who\u2019s telling?";
  ctx.fillText(text, 0, 0);
  const w = ctx.measureText(text).width;
  ctx.restore();
  const from: [number, number] = [x + w + 14, y + 22];
  const to: [number, number] = [x + w + 44, y + 64];
  pencilCurve(ctx, from, [x + w + 46, y + 18], to, seed, 0.6);
  pencilHead(ctx, to, Math.PI / 2 + 0.25, 11, seed + 1, 0.6);
  return 52;
}

/** A little pencil wobble: a line drawn in two light passes, as by hand. */
function pencilLine(ctx: Ctx, pts: [number, number][], seed: number, alpha = 0.7, width = 2.2) {
  const r = rng(seed);
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (let pass = 0; pass < 2; pass++) {
    ctx.strokeStyle = `rgba(78,72,68,${(pass ? 0.55 : 1) * alpha})`;
    ctx.lineWidth = pass ? width * 0.6 : width;
    ctx.beginPath();
    pts.forEach(([x, y], i) => {
      const jx = (r() - 0.5) * 1.6;
      const jy = (r() - 0.5) * 1.6;
      if (i === 0) ctx.moveTo(x + jx, y + jy);
      else ctx.lineTo(x + jx, y + jy);
    });
    ctx.stroke();
  }
  ctx.restore();
}

/** A smooth pencil curve through quadratic control points. */
function pencilCurve(ctx: Ctx, from: [number, number], ctrl: [number, number], to: [number, number], seed: number, alpha = 0.7) {
  const pts: [number, number][] = [];
  for (let i = 0; i <= 14; i++) {
    const t = i / 14;
    const a = (1 - t) * (1 - t);
    const b = 2 * (1 - t) * t;
    const c = t * t;
    pts.push([a * from[0] + b * ctrl[0] + c * to[0], a * from[1] + b * ctrl[1] + c * to[1]]);
  }
  pencilLine(ctx, pts, seed, alpha);
}

/** An arrowhead in pencil at `tip`, pointing along `angle`. */
function pencilHead(ctx: Ctx, tip: [number, number], angle: number, size: number, seed: number, alpha = 0.7, width = 2.2) {
  const a1 = angle + Math.PI - 0.5;
  const a2 = angle + Math.PI + 0.5;
  pencilLine(ctx, [[tip[0] + Math.cos(a1) * size, tip[1] + Math.sin(a1) * size], tip, [tip[0] + Math.cos(a2) * size, tip[1] + Math.sin(a2) * size]], seed, alpha, width);
}

/**
 * One label per telling, taped onto the page: the teller's puppet, a small
 * "Hear" and their name's version in handwriting. The `active` one is the
 * telling playing: full ink, the puppet mid-story, the name stitched under in
 * their thread. The others wait a shade lighter, ready to be chosen.
 */
export function drawTellerLabels(
  ctx: Ctx,
  tellers: readonly BookTeller[],
  rects: readonly LabelRect[],
  active: string | null,
  fonts: PageFonts,
  seed: number,
) {
  tellers.forEach((t, i) => {
    const rc = rects[i];
    const on = t.id === active;
    const r = rng(seed + i * 13);
    const angle = (i % 2 ? 1 : -1) * (0.018 + r() * 0.014);
    ctx.save();
    ctx.translate(rc.x + rc.w / 2, rc.y + rc.h / 2);
    ctx.rotate(angle);
    const w = rc.w;
    const h = rc.h;
    // The label: a cut card, a soft shadow under it on the page.
    ctx.shadowColor = on ? "rgba(74,47,29,0.3)" : "rgba(74,47,29,0.2)";
    ctx.shadowBlur = on ? 12 : 7;
    ctx.shadowOffsetY = on ? 4 : 2.5;
    ctx.fillStyle = on ? "#fbf7ee" : "#f1e8d6";
    ctx.beginPath();
    ctx.roundRect(-w / 2, -h / 2, w, h, 5);
    ctx.fill();
    ctx.shadowColor = "transparent";
    // Card grain and a faint ruled line, as on an index card.
    for (let k = 0; k < 140; k++) {
      ctx.fillStyle = r() > 0.5 ? "rgba(120,90,60,0.05)" : "rgba(255,255,255,0.18)";
      ctx.fillRect(-w / 2 + r() * w, -h / 2 + r() * h, 1 + r() * 2, 1);
    }
    ctx.strokeStyle = on ? rgba(t.color, 0.38) : "rgba(169,192,211,0.45)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(-w / 2 + 8, -h / 2 + 42);
    ctx.lineTo(w / 2 - 8, -h / 2 + 42);
    ctx.stroke();

    // The puppet, standing at the label's left.
    // Small enough that a raised hand stays under the label's top line.
    const ph = h * 0.8;
    drawPuppetAvatar(ctx, -w / 2 + 10, -h / 2 + h * 0.16, ph, t.color, on);
    if (on) {
      // Sound lines by the puppet's raised hand, in the teller's thread.
      ctx.save();
      ctx.strokeStyle = t.color;
      ctx.lineCap = "round";
      ctx.lineWidth = 2.6;
      const cx = -w / 2 + 10 + ph * 0.66;
      const cy = -h / 2 + h * 0.16 + ph * 0.14;
      for (let k = 0; k < 2; k++) {
        ctx.globalAlpha = 0.85 - k * 0.3;
        ctx.beginPath();
        ctx.arc(cx, cy, 9 + k * 8, -0.75, 0.15);
        ctx.stroke();
      }
      ctx.restore();
    }

    // "Hear" small, then "Ana's version" in hand.
    const tx = -w / 2 + ph * 0.78 + 16;
    const maxW = w / 2 - tx - 12;
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = on ? INK_SOFT : INK_FAINT;
    ctx.font = `700 17px ${fonts.sans}`;
    setSpacing(ctx, 3.5);
    ctx.fillText(on ? "NOW TELLING" : "HEAR", tx, -h / 2 + 32);
    setSpacing(ctx, 0);
    ctx.fillStyle = on ? INK : INK_SOFT;
    let size = 58;
    const name = `${t.name}’s`;
    ctx.font = `600 ${size}px ${fonts.hand}`;
    while (size > 26 && ctx.measureText(name).width > maxW) {
      size -= 2;
      ctx.font = `600 ${size}px ${fonts.hand}`;
    }
    ctx.fillText(name, tx, -h / 2 + 42 + size * 0.92, maxW);
    ctx.font = `500 36px ${fonts.hand}`;
    ctx.fillStyle = on ? INK_SOFT : INK_FAINT;
    ctx.fillText("version", tx + 2, h / 2 - 22, maxW);
    if (on) {
      const nameW = Math.min(maxW, ctx.measureText("version").width + 8);
      drawStitchLine(ctx, tx, h / 2 - 10, tx + nameW, h / 2 - 10, t.color, 2.6, 10, 7);
    }

    // Tape across the top, tinted with the teller's thread.
    // Tape over the top corner, clear of the words.
    const tw = Math.min(104, w * 0.32);
    drawTape(ctx, w / 2 - tw * 0.3, -h / 2 + 8, tw, 30, 0.62 + (r() - 0.5) * 0.12, seed + i * 7, tapeTint(t.color, on));
    ctx.restore();
  });
}

/** "#rrggbb" at alpha `a`. */
function rgba(hex: string, a: number): string {
  const n = parseInt(hex.slice(1, 7), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

/** Washi tape in the teller's thread, translucent. */
function tapeTint(color: string, on: boolean): string {
  return rgba(mix(color, "#e9dcbd", on ? 0.55 : 0.72), on ? 0.8 : 0.66);
}

/* ------------------------------------------------------------------------ */
/* Pull tabs                                                                 */
/* ------------------------------------------------------------------------ */

/**
 * A pull tab's geometry on the page, page px: the slit it comes out of (a
 * vertical cut at `slitX`, centred on `y`), how far the lever sticks out to
 * the right at rest (`length`), how wide it is, and how far it pulls.
 */
export interface PullTabGeometry {
  slitX: number;
  y: number;
  length: number;
  width: number;
  travel: number;
}

const PAGE_W_PX = 1000;
const PAGE_H_PX = 1300;

/** The lever's size, page px: big enough to read and grab on a laptop, the grip a thumb wide. */
const LEVER = { length: 150, width: 76, travel: 96 } as const;
/** Kept this far inside the page's edges (the deckled edge is ~24 px). */
const LEVER_MARGIN = { side: 40, head: 150, foot: 130 } as const;

export function pullTabGeometry(tab: BookPullTab): PullTabGeometry {
  const { length, width, travel } = LEVER;
  // Pulled all the way, the lever still ends on the page.
  const slitX = Math.min(Math.max(tab.at[0] * PAGE_W_PX, 160), PAGE_W_PX - LEVER_MARGIN.side - length - travel);
  const y = Math.min(Math.max(tab.at[1] * PAGE_H_PX, LEVER_MARGIN.head), PAGE_H_PX - LEVER_MARGIN.foot);
  return { slitX, y, length, width, travel };
}

/** The slit in the stage page, and the tab's label pencilled beside it. */
export function drawPullTabPrint(ctx: Ctx, tab: BookPullTab, fonts: PageFonts, seed: number) {
  const g = pullTabGeometry(tab);
  const half = g.width / 2 + 7;
  // The slit: a clean knife cut, a dark line with a lit lip under it.
  ctx.save();
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(50,32,20,0.55)";
  ctx.lineWidth = 3.2;
  ctx.beginPath();
  ctx.moveTo(g.slitX, g.y - half);
  ctx.lineTo(g.slitX, g.y + half);
  ctx.stroke();
  ctx.strokeStyle = "rgba(255,250,238,0.8)";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(g.slitX + 2.4, g.y - half + 2);
  ctx.lineTo(g.slitX + 2.4, g.y + half - 2);
  ctx.stroke();
  ctx.restore();

  // The label, pencilled above, ending over the tab; a little curved pencil
  // arrow from it down to where the lever pulls.
  ctx.save();
  ctx.fillStyle = "rgba(78,72,68,0.82)";
  ctx.font = `500 35px ${fonts.hand}`;
  ctx.textAlign = "right";
  ctx.textBaseline = "alphabetic";
  const endX = g.slitX + g.length + 8;
  const words = tab.label.trim();
  const lines = ctx.measureText(words).width > 360 ? splitInTwo(words) : [words];
  const lineH = 35;
  const baseY = g.y - g.width / 2 - 36 - (lines.length - 1) * lineH;
  ctx.translate(endX, baseY);
  ctx.rotate(-0.025);
  lines.forEach((l, i) => ctx.fillText(l, 0, i * lineH));
  ctx.restore();
  const from: [number, number] = [endX + 12, g.y - g.width / 2 - 52];
  const to: [number, number] = [endX + 30, g.y - g.width / 2 - 10];
  pencilCurve(ctx, from, [endX + 34, from[1] + 4], to, seed, 0.62);
  pencilHead(ctx, to, Math.atan2(to[1] - (from[1] + 4), to[0] - (endX + 34)) + 0.3, 11, seed + 1, 0.62);
}

function splitInTwo(text: string): string[] {
  const words = text.split(/\s+/);
  let best = 1;
  let bestDiff = Infinity;
  for (let i = 1; i < words.length; i++) {
    const a = words.slice(0, i).join(" ").length;
    const b = words.slice(i).join(" ").length;
    if (Math.abs(a - b) < bestDiff) {
      bestDiff = Math.abs(a - b);
      best = i;
    }
  }
  return [words.slice(0, best).join(" "), words.slice(best).join(" ")];
}

/**
 * The lever's top face, the whole strip (`w` × `h` px, the grip end at the
 * right; `shown` px of it at the right stick out of the slit at rest): cream
 * card stitched round its edge in thread, folded back on itself at the grip
 * (the fold shows as a crease and the card's red back), with two chevrons
 * pointing the way it pulls.
 */
export function drawLeverFace(w: number, h: number, shown: number, fold: number, seed: number, cardPx: number): HTMLCanvasElement {
  const { c, ctx } = canvas(w, h);
  const r = rng(seed);
  // Canvas px per page px, so thread and grain print at the page's own scale.
  const u = h / cardPx;
  ctx.fillStyle = "#f4ead7";
  ctx.fillRect(0, 0, w, h);
  for (let k = 0; k < (w * h) / (70 * u * u); k++) {
    ctx.fillStyle = r() > 0.55 ? "rgba(120,90,60,0.06)" : "rgba(255,255,255,0.22)";
    ctx.fillRect(r() * w, r() * h, (1 + r() * 2) * u, u);
  }
  // A cut card's rim reads a hair darker along the long edges.
  ctx.fillStyle = "rgba(120,86,52,0.18)";
  ctx.fillRect(0, 0, w, 2 * u);
  ctx.fillRect(0, h - 2 * u, w, 2 * u);

  // The folded-over end: the card's red back, and the crease.
  const fx = w - fold;
  ctx.fillStyle = ACCENT;
  ctx.fillRect(fx, 0, fold, h);
  for (let k = 0; k < (fold * h) / (50 * u * u); k++) {
    ctx.fillStyle = r() > 0.5 ? "rgba(255,230,200,0.08)" : "rgba(60,20,10,0.08)";
    ctx.fillRect(fx + r() * fold, r() * h, (1 + r() * 2) * u, u);
  }
  ctx.fillStyle = "rgba(40,20,10,0.28)";
  ctx.fillRect(fx, 0, 2.5 * u, h);
  ctx.fillStyle = "rgba(255,240,220,0.4)";
  ctx.fillRect(fx + 2.5 * u, 0, 1.2 * u, h);

  // Running stitch round the card, in the book's red thread: along both
  // long edges from inside the slit to the crease.
  const inset = Math.round(h * 0.17);
  const x0 = w - shown - 40 * u;
  const thread = "rgba(185,87,61,0.9)";
  drawStitchLine(ctx, x0, inset, fx - 9 * u, inset, thread, 2.4 * u, 9 * u, 6 * u);
  drawStitchLine(ctx, x0, h - inset, fx - 9 * u, h - inset, thread, 2.4 * u, 9 * u, 6 * u);

  // Two chevrons by the grip, pointing the way it pulls.
  ctx.save();
  ctx.strokeStyle = INK_SOFT;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = Math.max(3, h * 0.07);
  const ch = h * 0.2;
  for (let i = 0; i < 2; i++) {
    const x = fx - 16 * u - i * ch * 1.15;
    ctx.globalAlpha = i === 0 ? 0.9 : 0.5;
    ctx.beginPath();
    ctx.moveTo(x - ch * 0.75, h / 2 - ch);
    ctx.lineTo(x, h / 2);
    ctx.lineTo(x - ch * 0.75, h / 2 + ch);
    ctx.stroke();
  }
  ctx.restore();
  return c;
}

/**
 * The cut page's lip shading the card just out of the slit: an alpha ramp,
 * opaque at the slit (x = 0) and gone by the right edge.
 */
export function drawLipShade(): HTMLCanvasElement {
  const { c, ctx } = canvas(64, 4);
  const g = ctx.createLinearGradient(0, 0, 64, 0);
  g.addColorStop(0, "#fff");
  g.addColorStop(0.35, "#888");
  g.addColorStop(1, "#000");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 64, 4);
  return c;
}
