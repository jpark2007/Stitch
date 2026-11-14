/**
 * The pages, drawn in code on <canvas>: the story page, the empty stage page,
 * the title page and blank pages. Paper, type and tape come from paperArt.ts,
 * photos from photoArt.ts, the cover and binding from coverArt.ts.
 */

import { ACCENT, canvas, drawStitchLine, INK, INK_FAINT, INK_SOFT, paper, setSpacing, wrap, type Ctx, type PageFonts } from "./paperArt";
import { drawPhoto } from "./photoArt";
import type { BookCover, BookSpread } from "./types";


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
