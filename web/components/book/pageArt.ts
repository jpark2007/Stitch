/**
 * The pages, drawn in code on <canvas>: the story page, the empty stage page,
 * the title page and blank pages. Paper, type and tape come from paperArt.ts,
 * photos from photoArt.ts, the cover and binding from coverArt.ts.
 */

import { ACCENT, canvas, drawGutterStitches, drawStitchLine, INK, INK_FAINT, INK_SOFT, paper, setSpacing, wrap, type Ctx, type PageFonts } from "./paperArt";
import { drawLabelsPrompt, drawPullTabPrint, drawPuppetAvatar, drawTellerLabels, layoutTellerLabels, tellerLabelsHeight, type LabelRect } from "./labelArt";
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

/** A drawn story page, and where its teller labels are (page px) for hit testing. */
export interface StoryPageArt {
  canvas: HTMLCanvasElement;
  labels: LabelRect[];
}

/**
 * Left page: year (or `spread.dateLine`), title, storybook text, tellers and
 * taped-in photos. With `spread.tellings`, the tellers are taped labels to
 * choose a telling by, the `active` one inked as playing. Text of any length
 * fits (layoutStory): a long one is set smaller, leaves out photos it leaves
 * no room for, and past that is cut with "…".
 */
export async function drawStoryPage(
  spread: BookSpread,
  page: number,
  fonts: PageFonts,
  seed: number,
  active: string | null = null,
): Promise<StoryPageArt> {
  const W = PAGE_PX_W;
  const H = PAGE_PX_H;
  const { c, ctx: pageCtx } = canvas(W, H);
  paper(pageCtx, W, H, { seed, gutter: "right" });
  drawGutterStitches(pageCtx, W, H, "right");
  if (spread.kind === "end") {
    drawEndPage(pageCtx, spread, fonts);
    pageNumber(pageCtx, page, "left", fonts);
    return { canvas: c, labels: [] };
  }
  const left = 96;
  const right = W - 134;
  const width = right - left;
  const tellings = spread.tellings ?? [];
  const teller = tellings.find((t) => t.id === active) ?? tellings[0];
  const text = (spread.text ?? "").trim() || teller?.text?.trim() || "";
  const date = spread.dateLine?.trim() || String(spread.year);

  // Lay the page out before drawing it: the title, then the words at the
  // largest size that leaves the photos their room (StoryLayout).
  pageCtx.font = TITLE_FONT(fonts);
  setSpacing(pageCtx, -0.8);
  const titleLines = clampLines(pageCtx, wrap(pageCtx, spread.title ?? "", width), TITLE_MAX_LINES, () => width);
  setSpacing(pageCtx, 0);
  const bodyTop = TITLE_TOP + titleLines.length * TITLE_LEAD + 8 + 58;
  const plan = layoutStory(pageCtx, text, fonts, {
    width,
    top: bodyTop,
    tail: storyTailHeight(spread),
    photos: (spread.photos ?? []).slice(0, 2).map((p) => photoAspect(p.aspect)),
  });
  const photos = (spread.photos ?? []).slice(0, plan.photos);
  // Without photos the words sit on their own layer, to be set in the
  // page's optical middle rather than hung from its head.
  const layer = photos.length ? null : canvas(W, H);
  const ctx = layer ? layer.ctx : pageCtx;

  // Year (or the spread's dateLine), handwritten in the accent ink.
  ctx.save();
  ctx.translate(left - 4, 168);
  ctx.rotate(-0.035);
  ctx.fillStyle = ACCENT;
  ctx.font = `600 76px ${fonts.hand}`;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  if (spread.dateLine?.trim()) {
    // A few words, not four digits: a size down if they would run long.
    let size = 76;
    while (size > 48 && ctx.measureText(date).width > width) {
      size -= 4;
      ctx.font = `600 ${size}px ${fonts.hand}`;
    }
    ctx.fillText(date, 0, 0, width);
  } else ctx.fillText(date, 0, 0);
  ctx.restore();

  // Title.
  ctx.fillStyle = INK;
  ctx.font = TITLE_FONT(fonts);
  setSpacing(ctx, -0.8);
  ctx.textAlign = "left";
  let y = TITLE_TOP;
  for (const line of titleLines) {
    ctx.fillText(line, left, y);
    y += TITLE_LEAD;
  }
  setSpacing(ctx, 0);

  // A short stitched rule in thread.
  y += 8;
  drawStitchLine(ctx, left + 2, y, left + 190, y, ACCENT, 3.2, 16, 11);
  y += 58;

  // Storybook text, opening on a two-line drop cap like a printed storybook.
  // A line or two is set larger, so a short page still reads as a page.
  const { lead, capW, lines: body } = plan.body;
  ctx.font = `600 ${plan.body.capSize}px ${fonts.display}`;
  ctx.fillStyle = ACCENT;
  // The cap's baseline sits on the second line's baseline.
  ctx.fillText(text.charAt(0), left - 2, y + lead);
  ctx.fillStyle = INK_SOFT;
  ctx.font = BODY_FONT(fonts, plan.body.size);
  body.forEach((line, i) => {
    ctx.fillText(line, left + (i < 2 ? capW : 0), y);
    y += lead;
  });
  // The drop cap is two lines tall, however short the text.
  if (text && body.length < 2) y += lead * (2 - body.length);

  let labels: LabelRect[] = [];
  if (tellings.length > 1) {
    // One taped label per telling, to hear the same scene told another way.
    y += 58;
    y += drawLabelsPrompt(ctx, left + 4, y, fonts, seed + 40) + 16;
    labels = layoutTellerLabels(tellings, left + 4, width - 8, y);
    drawTellerLabels(ctx, tellings, labels, active, fonts, seed + 50);
    y += tellerLabelsHeight(tellings.length) + 6;
  } else if (spread.tellers?.length) {
    y += 14;
    ctx.fillStyle = INK_FAINT;
    ctx.font = `500 38px ${fonts.hand}`;
    const names = spread.tellers.length > 1
      ? `${spread.tellers.slice(0, -1).join(", ")} & ${spread.tellers[spread.tellers.length - 1]}`
      : spread.tellers[0];
    ctx.fillText(`— as told by ${names}`, left, y);
    y += 20;
  }

  if (layer) {
    // The block runs from the year (~110) to y; centre it a little high.
    const room = H - 150 - 100;
    const dy = Math.max(0, Math.round(((room - (y - 110)) / 2) * 0.72));
    pageCtx.drawImage(layer.c, 0, dy);
    labels = labels.map((r) => ({ ...r, y: r.y + dy }));
  }

  // Photos, taped into the space that is left (layoutStory kept it at least
  // PHOTO_MIN_ROOM, leaving out the ones it could not).
  const top = photoTop(y);
  if (photos.length === 1) {
    const pw = Math.min(520, photoRoom(1, y) * photoAspect(photos[0].aspect) * 0.9);
    if (pw > 0) await drawPhoto(ctx, photos[0], left + (width - pw) / 2 + 10, top, pw, -0.03, seed + 1, fonts, "top");
  } else if (photos.length >= 2) {
    const room = photoRoom(2, y);
    const w0 = Math.min(430, room * photoAspect(photos[0].aspect) * 0.82);
    const w1 = Math.min(330, room * photoAspect(photos[1].aspect) * 0.72);
    if (w0 > 0) await drawPhoto(ctx, photos[0], left - 6, top, w0, -0.055, seed + 1, fonts, "top");
    if (w1 > 0) await drawPhoto(ctx, photos[1], right - w1 + 8, top + Math.min(70, room * 0.16), w1, 0.06, seed + 2, fonts, "corners");
  }

  pageNumber(pageCtx, page, "left", fonts);
  return { canvas: c, labels };
}

/* ------------------------------------------------------------------------ */
/* Story layout: fitting any length of text on the page                      */
/* ------------------------------------------------------------------------ */

const TITLE_FONT = (fonts: PageFonts) => `600 64px ${fonts.display}`;
const BODY_FONT = (fonts: PageFonts, size: number) => `400 ${size}px ${fonts.display}`;
const TITLE_TOP = 262;
const TITLE_LEAD = 72;
/** A longer title is cut to this many lines, the last ending in "…". */
const TITLE_MAX_LINES = 3;
/** The lowest the words (and tellers) may run on a page without photos. */
const TEXT_FOOT = PAGE_PX_H - 150;
/**
 * The least room (page px, from the photos' top to the foot margin) a taped
 * photo is given. Less than this and the photo is left out, rather than
 * shrunk to a postage stamp (or, past zero, to nothing at all).
 */
export const PHOTO_MIN_ROOM = 220;
/**
 * Body sizes, [font px, line lead px], largest first. A short text opens at
 * the large size; a long one steps down to a readable floor (the last) to
 * fit, and past that is cut, its last line ending in "…".
 */
const SHORT_SIZE = [37, 54] as const;
const BODY_SIZES = [
  [31, 46],
  [28, 42],
  [26, 39],
  [24, 36],
] as const;
/** More text than any page holds, even at the floor: the rest is cut before wrapping. */
const TEXT_CAP = 4000;
/** The "Who's telling?" pencil note over the labels (labelArt.drawLabelsPrompt). */
const PROMPT_H = 52;

/** A photo's aspect (width / height), 4/3 when missing or not a usable number. */
export function photoAspect(aspect: number | undefined): number {
  return typeof aspect === "number" && Number.isFinite(aspect) && aspect > 0 ? Math.min(4, Math.max(0.25, aspect)) : 4 / 3;
}

/** Where the photos are taped, for words ending at y. */
function photoTop(y: number) {
  return Math.max(y + 46, 700);
}

/** The photos' room under words ending at y (two photos stand a little lower). */
export function photoRoom(count: number, y: number) {
  return PAGE_PX_H - (count >= 2 ? 140 : 150) - photoTop(y);
}

/** How far the tellers' line, or the tellings' labels, run below the words. */
export function storyTailHeight(spread: Pick<BookSpread, "tellings" | "tellers">): number {
  const tellings = spread.tellings ?? [];
  if (tellings.length > 1) return 58 + PROMPT_H + 16 + tellerLabelsHeight(tellings.length) + 6;
  if (spread.tellers?.length) return 34;
  return 0;
}

/** The storybook text set at one size. */
export interface StoryBody {
  /** Font size and line lead, page px. */
  size: number;
  lead: number;
  /** The drop cap's size and the indent it gives the first two lines. */
  capSize: number;
  capW: number;
  /** The text after the drop cap, line by line. */
  lines: string[];
  /** Height the words take on the page, the drop cap's two lines at least. */
  height: number;
  /** True when the text did not fit and was cut ("…"). */
  clamped: boolean;
}

/** The page's layout: the words, and how many photos still have room for them. */
export interface StoryLayout {
  body: StoryBody;
  photos: number;
}

export interface StoryLayoutOptions {
  /** The measure, page px. */
  width: number;
  /** The first body line's baseline. */
  top: number;
  /** What runs under the words (storyTailHeight). */
  tail: number;
  /** Each photo's aspect, in the order they are taped in. */
  photos: readonly number[];
}

function setBody(ctx: Ctx, text: string, fonts: PageFonts, width: number, [size, lead]: readonly [number, number], tidy: boolean): StoryBody {
  const capSize = Math.round(lead * 2.3);
  ctx.font = `600 ${capSize}px ${fonts.display}`;
  const first = text.charAt(0);
  const capM = ctx.measureText(first);
  // Tight to the glyph's ink, so a slim "I" still sits close to its word.
  // A one-letter first word ("A day…") keeps a word space after the cap.
  const capW = Math.max(capM.actualBoundingBoxRight || 0, 18) + (/^\s/.test(text.slice(1)) ? 20 : 4);
  ctx.font = BODY_FONT(fonts, size);
  const rest = text.slice(1);
  let lines = wrap(ctx, rest, (i) => (i < 2 ? width - capW : width));
  // No widows: narrow the measure a little until the last line has company.
  for (let k = 1; tidy && k <= 10 && lines.length > 2 && !lines[lines.length - 1].includes(" "); k++) {
    const w = width - k * 14;
    lines = wrap(ctx, rest, (i) => (i < 2 ? w - capW : w));
  }
  const height = text ? Math.max(lines.length, 2) * lead : 0;
  return { size, lead, capSize, capW, lines, height, clamped: false };
}

/**
 * Cut `lines` to `max`, ending the last kept line in "…" (whole words, its
 * trailing punctuation dropped) within `widthFor` that line.
 */
function clampLines(ctx: Ctx, lines: string[], max: number, widthFor: (line: number) => number, cut = false): string[] {
  if (lines.length <= max && !cut) return lines;
  const kept = lines.slice(0, Math.max(1, max));
  const i = kept.length - 1;
  const words = (kept[i] ?? "").split(" ");
  const close = (ws: string[]) => `${ws.join(" ").replace(/[\s,;:.!?—–-]+$/, "")}…`;
  let last = close(words);
  while (words.length > 1 && ctx.measureText(last).width > widthFor(i)) {
    words.pop();
    last = close(words);
  }
  kept[i] = last;
  return kept;
}

/**
 * Fit a story page's text, for any length. Words and photos are set as they
 * always were while they fit; past that the text steps down through
 * BODY_SIZES, keeping each photo at least PHOTO_MIN_ROOM of room, then
 * leaves photos out (the last first), and at the floor size with no photos
 * is cut, ending in "…". Every photo kept has a positive size. `ctx` is only
 * measured with; its font is left changed.
 */
export function layoutStory(ctx: Ctx, text: string, fonts: PageFonts, o: StoryLayoutOptions): StoryLayout {
  const cut = text.length > TEXT_CAP;
  const words = cut ? text.slice(0, TEXT_CAP) : text;
  const sizes = words.length < 150 ? [SHORT_SIZE, ...BODY_SIZES] : BODY_SIZES;
  const fits = (b: StoryBody, n: number) => {
    const end = o.top + b.height + o.tail;
    return n === 0 ? end <= TEXT_FOOT : photoRoom(n, end) >= PHOTO_MIN_ROOM;
  };
  if (!cut) {
    for (let n = Math.min(2, o.photos.length); n >= 0; n--) {
      for (const size of sizes) {
        const plain = setBody(ctx, words, fonts, o.width, size, false);
        if (!fits(plain, n)) continue;
        const tidy = setBody(ctx, words, fonts, o.width, size, true);
        return { body: fits(tidy, n) ? tidy : plain, photos: n };
      }
    }
  }
  // Too long for the page at any size: the floor size, no photos, cut.
  const body = setBody(ctx, words, fonts, o.width, sizes[sizes.length - 1], false);
  const max = Math.max(2, Math.floor((TEXT_FOOT - o.tail - o.top) / body.lead));
  const lines = clampLines(ctx, body.lines, max, (i) => (i < 2 ? o.width - body.capW : o.width), cut);
  return { body: { ...body, lines, height: Math.max(lines.length, 2) * body.lead, clamped: true }, photos: 0 };
}

/**
 * The last page: "The end" in hand, a stitched rule, a small line (the
 * spread's text), and the tellers' paper puppets standing side by side.
 */
function drawEndPage(ctx: Ctx, spread: BookSpread, fonts: PageFonts) {
  const W = PAGE_PX_W;
  const H = PAGE_PX_H;
  const cx = W / 2 - 18;
  const top = H * 0.36;
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = INK;
  ctx.font = `600 96px ${fonts.hand}`;
  ctx.save();
  ctx.translate(cx, top);
  ctx.rotate(-0.03);
  ctx.fillText(spread.title || "The end", 0, 0);
  ctx.restore();
  drawStitchLine(ctx, cx - 80, top + 46, cx + 80, top + 46, ACCENT, 3.2, 16, 11);
  if (spread.text) {
    ctx.fillStyle = INK_SOFT;
    ctx.font = `italic 400 32px ${fonts.display}`;
    // A small line; a long one is cut before it reaches the puppets.
    clampLines(ctx, wrap(ctx, spread.text, 560), 3, () => 560).forEach((line, i) => ctx.fillText(line, cx, top + 118 + i * 44));
  }
  // The tellers, side by side, as on the tree.
  const tellers = spread.tellings ?? [];
  const h = 230;
  const step = 132;
  const x0 = cx - ((tellers.length - 1) * step) / 2 - (h * 100) / 130 / 2;
  tellers.slice(0, 5).forEach((t, i) => drawPuppetAvatar(ctx, x0 + i * step, top + 190, h, t.color, false));
}

/**
 * The stage slot on the right page, in page pixels (x from the gutter edge,
 * y from the head). StoryPage.tsx turns it into world units for the stage.
 */
export const STAGE_SLOT_PX = { x0: 134, x1: PAGE_PX_W - 96, y0: 104, y1: PAGE_PX_H - 132 } as const;

/**
 * Right page: an empty, clearly marked slot where the pop-up stage mounts.
 * Under a real pop-up (`spread.stage`) the page is printed plain, with the
 * slits and pencilled labels of its pull tabs.
 */
export function drawStagePage(spread: BookSpread, page: number, fonts: PageFonts, seed: number) {
  const W = PAGE_PX_W;
  const H = PAGE_PX_H;
  const { c, ctx } = canvas(W, H);
  paper(ctx, W, H, { seed, gutter: "left" });
  drawGutterStitches(ctx, W, H, "left");
  if (spread.kind === "end") {
    pageNumber(ctx, page, "right", fonts);
    return c;
  }
  if (spread.stage) {
    (spread.stage.pullTabs ?? []).forEach((tab, i) => drawPullTabPrint(ctx, tab, fonts, seed + 20 + i));
    pageNumber(ctx, page, "right", fonts);
    return c;
  }

  const { x0, x1, y0, y1 } = STAGE_SLOT_PX;
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
  ctx.fillText(`${spread.title.toUpperCase()} · ${spread.dateLine?.trim() || spread.year}`.slice(0, 60), (x0 + x1) / 2, (y0 + y1) * 0.5 + 52);
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
  drawGutterStitches(ctx, W, H, "left");
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
