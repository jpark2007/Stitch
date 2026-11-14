/**
 * Click-to-explain on a moment's stage (DESIGN.md "Click any character or
 * element and it explains, in an animated way, why it matters"): the pure
 * half. No React, no three; tested in node (explain.test.ts).
 *
 *   explainItems(explanations, scene, cast)   what can be explained on this stage
 *   parseTarget("puppet:dad")                 { kind: "puppet", id: "dad" } (or null)
 *   onSet(x, wing)                            a cutout at stage x is on the set now
 *   layoutNote(title, text, measure)          the note's lines and card size
 *   placeNote(input, out)                     where the note stands so it stays on screen
 *   explainStep(open, event)                  which note is open after a click, key or turn
 *   resumesAfter(reason)                      whether closing hands the scene back to play
 *   noteRise(ms) / noteFold(ms) / sewAt(...)  the pop-up's motion over time
 *
 * The 3D half (moment/ExplainNote.tsx) hit-tests the cutouts, raises the
 * note on its paper hinge and sews the thread; it asks this module every
 * decision it can.
 */

import type { Explanation, Person, Prop } from "@/lib/types";

/* ------------------------------------------------------------------------ */
/* Targets                                                                   */
/* ------------------------------------------------------------------------ */

export type ExplainKind = "puppet" | "prop";

export interface ExplainTarget {
  kind: ExplainKind;
  id: string;
  /** The canonical target, "puppet:<id>" or "prop:<id>". */
  target: string;
}

/** Ids are what the compiler writes: no spaces, no separators of their own. */
const TARGET = /^\s*(puppet|prop):([A-Za-z0-9_-][A-Za-z0-9_.-]*)\s*$/;

/** A timeline target string as {kind, id}; null when it is anything else. */
export function parseTarget(target: unknown): ExplainTarget | null {
  if (typeof target !== "string") return null;
  const m = TARGET.exec(target);
  if (!m) return null;
  const kind = m[1] as ExplainKind;
  // A socket reference ("puppet:dad.hand_r") names a hand, not a cast member.
  if (m[2].includes(".")) return null;
  return { kind, id: m[2], target: `${kind}:${m[2]}` };
}

export interface ExplainItem extends ExplainTarget {
  /** The note's heading: the person's or the prop's name. */
  title: string;
  text: string;
  /** Thread colour: the person's thread (character.color), or the prop's paper. */
  color: string | null;
}

/** "grandma_rosa" -> "Grandma Rosa", for cast the scene data does not name. */
export function titleFromId(id: string): string {
  return id
    .split(/[_-]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

/** The longest explanation shown; anything longer is cut at a word with an ellipsis. */
export const MAX_TEXT = 640;

function clip(text: string): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= MAX_TEXT) return t;
  const cut = t.slice(0, MAX_TEXT);
  const at = cut.lastIndexOf(" ");
  return `${(at > MAX_TEXT * 0.7 ? cut.slice(0, at) : cut).replace(/[\s,;:.-]+$/, "")}…`;
}

/**
 * What can be explained on this stage: one item per explanation whose
 * target the timeline casts (a puppet or prop it has tracks for), headed by
 * the person's or prop's name. Malformed targets, empty texts and targets
 * the stage does not cast are ignored; the first explanation of a target
 * wins. Order follows the data.
 */
export function explainItems(
  explanations: readonly Explanation[] | null | undefined,
  scene: { people: readonly Pick<Person, "id" | "name" | "character">[]; props: readonly Pick<Prop, "id" | "name" | "color">[] },
  cast: { puppets: readonly string[]; props: readonly string[] },
): ExplainItem[] {
  if (!Array.isArray(explanations)) return [];
  const puppets = new Set(cast.puppets);
  const props = new Set(cast.props);
  const people = new Map(scene.people.map((p) => [p.id, p]));
  const things = new Map(scene.props.map((p) => [p.id, p]));
  const out: ExplainItem[] = [];
  const seen = new Set<string>();
  for (const e of explanations) {
    if (!e || typeof e !== "object") continue;
    const t = parseTarget((e as { target?: unknown }).target);
    const raw = (e as { text?: unknown }).text;
    if (!t || typeof raw !== "string" || seen.has(t.target)) continue;
    const text = clip(raw);
    if (!text) continue;
    if (t.kind === "puppet") {
      if (!puppets.has(t.id)) continue;
      const p = people.get(t.id);
      out.push({ ...t, title: p?.name?.trim() || titleFromId(t.id), text, color: p?.character?.color ?? null });
    } else {
      if (!props.has(t.id)) continue;
      const p = things.get(t.id);
      // A heading: "hymn books" -> "Hymn books".
      const name = p?.name?.trim();
      out.push({ ...t, title: name ? name[0].toUpperCase() + name.slice(1) : titleFromId(t.id), text, color: p?.color ?? null });
    }
    seen.add(t.target);
  }
  return out;
}

/**
 * A cutout whose centre stands at stage x is on the set right now: inside
 * the wings (|x| < 1, CONTRACTS.md "Stage space"), with a little to spare so
 * someone half through an entrance is not yet clickable. Not finite: not on.
 */
export function onSet(x: number, wing = 1, spare = 0.06): boolean {
  return Number.isFinite(x) && Math.abs(x) <= wing - spare;
}

/* ------------------------------------------------------------------------ */
/* The note's words                                                          */
/* ------------------------------------------------------------------------ */

/** Width of `text` set in the handwriting font at `px`. */
export type Measure = (text: string, px: number) => number;

export interface NoteLayoutOptions {
  /** Card width range (card px; the card is drawn at this scale, then sized on screen). */
  minW: number;
  maxW: number;
  /** Body and heading type sizes, and the smallest the body may shrink to. */
  font: number;
  minFont: number;
  title: number;
  /** Line height, as a multiple of the type size. */
  leading: number;
  pad: number;
  /** Lines of body text before the type shrinks, and the most ever set. */
  lines: number;
  maxLines: number;
}

export const NOTE_LAYOUT: NoteLayoutOptions = {
  minW: 250,
  maxW: 380,
  font: 25,
  minFont: 20,
  title: 34,
  leading: 1.12,
  pad: 24,
  lines: 5,
  maxLines: 12,
};

export interface NoteLayout {
  /** Card size, card px. */
  width: number;
  height: number;
  font: number;
  titleFont: number;
  title: string;
  /** Body lines, already wrapped (the last ends in an ellipsis when cut). */
  lines: string[];
  /** Where the body starts and how far apart its lines are (card px, baselines). */
  bodyTop: number;
  lineHeight: number;
  pad: number;
}

/** Greedy word wrap at `px` into `width`; a single word wider than the line is kept whole. */
export function wrapWords(text: string, px: number, width: number, measure: Measure): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let line = "";
  for (const w of words) {
    const t = line ? `${line} ${w}` : w;
    if (line && measure(t, px) > width) {
      lines.push(line);
      line = w;
    } else line = t;
  }
  if (line) lines.push(line);
  return lines;
}

/**
 * The note's words set on its card: the heading, then the text wrapped. A
 * short note stays narrow; a longer one widens to `maxW`, then the type
 * shrinks toward `minFont`, and past `maxLines` the text is cut with an
 * ellipsis. The card grows with its text, within those limits.
 */
export function layoutNote(title: string, text: string, measure: Measure, o: NoteLayoutOptions = NOTE_LAYOUT): NoteLayout {
  const head = title.trim() || "Why it matters";
  let font = o.font;
  let width = o.minW;
  let lines: string[] = [];
  const inner = (w: number) => w - 2 * o.pad;
  // Widen first (in steps), then shrink the type.
  for (;;) {
    lines = wrapWords(text, font, inner(width), measure);
    if (lines.length <= o.lines) break;
    if (width < o.maxW) {
      width = Math.min(o.maxW, width + 30);
      continue;
    }
    if (font > o.minFont) {
      font = Math.max(o.minFont, font - 1);
      continue;
    }
    break;
  }
  if (lines.length > o.maxLines) {
    lines = lines.slice(0, o.maxLines);
    let last = lines[o.maxLines - 1];
    while (last.includes(" ") && measure(`${last}…`, font) > inner(width)) last = last.slice(0, last.lastIndexOf(" "));
    lines[o.maxLines - 1] = `${last.replace(/[\s,;:.-]+$/, "")}…`;
  }
  // A short text: no wider than its longest line (and the heading) needs.
  let need = measure(head, o.title);
  for (const l of lines) need = Math.max(need, measure(l, font));
  width = Math.max(o.minW, Math.min(width, Math.ceil(need + 2 * o.pad + 4)));
  const lineHeight = font * o.leading;
  const titleH = o.title * 1.05;
  const bodyTop = o.pad + titleH + font * 0.55;
  const height = Math.ceil(bodyTop + lines.length * lineHeight + o.pad * 0.8);
  return { width, height, font, titleFont: o.title, title: head, lines, bodyTop, lineHeight, pad: o.pad };
}

/* ------------------------------------------------------------------------ */
/* The note writes itself                                                    */
/* ------------------------------------------------------------------------ */

export interface WriteStep {
  /** ms after the writing starts */
  delay: number;
  /** ms the word takes to write */
  dur: number;
}

export interface WriteOptions {
  perChar: number;
  /** A beat between words, and a longer one after a full stop. */
  gap: number;
  stop: number;
  /** The whole is scaled down to at most this, so a long note does not keep the reader waiting. */
  total: number;
}

export const WRITE: WriteOptions = { perChar: 24, gap: 45, stop: 140, total: 1900 };

/**
 * When each word writes itself in, as a hand would: time per letter, a beat
 * between words and a longer one after a full stop, the whole scaled down to
 * at most `total` ms.
 */
export function writeSchedule(words: readonly string[], o: WriteOptions = WRITE): WriteStep[] {
  const steps: WriteStep[] = [];
  let t = 0;
  for (const w of words) {
    const dur = Math.max(60, w.length * o.perChar);
    steps.push({ delay: t, dur });
    t += dur + (/[.!?…]["”’)]?$/.test(w) ? o.stop : /[,;:—]$/.test(w) ? o.gap * 1.8 : o.gap);
  }
  const end = steps.length ? steps[steps.length - 1].delay + steps[steps.length - 1].dur : 0;
  if (end > o.total) {
    const k = o.total / end;
    for (const s of steps) {
      s.delay = Math.round(s.delay * k);
      s.dur = Math.max(30, Math.round(s.dur * k));
    }
  }
  return steps;
}

/**
 * The note's ink, row by row (row 0 the heading and its stitch, then each
 * line of words): how far along its row each word reaches (0..1 of the
 * row's inked width) and when it is written. The note's face reveals its ink
 * up to a pen position, `row + fraction`, that writeAt reads off this plan.
 */
export interface WritePlan {
  /** Inked width of each row, card px (the heading's includes its stitch). */
  rowW: number[];
  /** Per word, in writing order: its row, where it starts and ends along it, and its timing. */
  row: Float32Array;
  f0: Float32Array;
  f1: Float32Array;
  delay: Float32Array;
  dur: Float32Array;
  /** ms from the first word starting to the last one finished. */
  total: number;
}

/** The heading's stitch: from the pad to this far along (card px), as the note's face draws it. */
export function underlineEnd(titleW: number, l: Pick<NoteLayout, "width" | "pad">): number {
  return Math.max(46, Math.min(titleW, l.width - 2 * l.pad) * 0.9);
}

export function writePlan(l: NoteLayout, measure: Measure, o: WriteOptions = WRITE): WritePlan {
  const rows = [l.title, ...l.lines];
  const rowW: number[] = [];
  const at: { row: number; f0: number; f1: number }[] = [];
  const words: string[] = [];
  rows.forEach((text, r) => {
    const px = r === 0 ? l.titleFont : l.font;
    const ws = text.split(/\s+/).filter(Boolean);
    let w = Math.max(1, measure(text, px));
    if (r === 0) w = Math.max(w, underlineEnd(w, l));
    rowW.push(w);
    let prefix = "";
    ws.forEach((word, i) => {
      const start = i === 0 ? 0 : measure(`${prefix} `, px);
      prefix = prefix ? `${prefix} ${word}` : word;
      // The heading's last word carries its stitch on to the row's end.
      const end = r === 0 && i === ws.length - 1 ? w : measure(prefix, px);
      at.push({ row: r, f0: Math.min(1, start / w), f1: Math.min(1, end / w) });
      words.push(word);
    });
  });
  const steps = writeSchedule(words, o);
  const n = steps.length;
  const plan: WritePlan = {
    rowW,
    row: new Float32Array(n),
    f0: new Float32Array(n),
    f1: new Float32Array(n),
    delay: new Float32Array(n),
    dur: new Float32Array(n),
    total: n ? steps[n - 1].delay + steps[n - 1].dur : 0,
  };
  for (let i = 0; i < n; i++) {
    plan.row[i] = at[i].row;
    plan.f0[i] = at[i].f0;
    plan.f1[i] = at[i].f1;
    plan.delay[i] = steps[i].delay;
    plan.dur[i] = steps[i].dur;
  }
  return plan;
}

/** Past every row: the whole note written. */
export const WRITTEN = 1e4;

/**
 * The pen's position `ms` into the writing, as `row + fraction along it`:
 * -1 before the first word, WRITTEN once the last is done. Allocates nothing.
 */
export function writeAt(plan: WritePlan, ms: number): number {
  const n = plan.delay.length;
  if (n === 0 || ms >= plan.total) return WRITTEN;
  if (ms < 0) return -1;
  // The last word started by now (words start in order).
  let i = 0;
  while (i + 1 < n && plan.delay[i + 1] <= ms) i++;
  const u = Math.min(1, Math.max(0, (ms - plan.delay[i]) / Math.max(1, plan.dur[i])));
  return plan.row[i] + plan.f0[i] + (plan.f1[i] - plan.f0[i]) * u;
}

/* ------------------------------------------------------------------------ */
/* Where the note stands                                                     */
/* ------------------------------------------------------------------------ */

/** A screen rectangle, CSS px, y down. */
export interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export const rect = (x0 = 0, y0 = 0, x1 = 0, y1 = 0): Rect => ({ x0, y0, x1, y1 });

const area = (r: Rect) => Math.max(0, r.x1 - r.x0) * Math.max(0, r.y1 - r.y0);

function overlap(a: Rect, b: Rect): number {
  const w = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
  const h = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
  return w > 0 && h > 0 ? w * h : 0;
}

/** How far (px) `r` reaches outside `view`, summed over its four sides. */
function outside(r: Rect, view: Rect): number {
  return Math.max(0, view.x0 - r.x0) + Math.max(0, r.x1 - view.x1) + Math.max(0, view.y0 - r.y0) + Math.max(0, r.y1 - view.y1);
}

export interface PlaceInput {
  /** The clicked cutout on screen. */
  target: Rect;
  /** Where the whole note must stay: the canvas less its margins (and any chrome over it). */
  view: Rect;
  /**
   * What the note keeps clear of (the printed caption): wholly clear
   * wherever any spot allows (it shrinks, slides or stands elsewhere first);
   * failing that, overlapped as little as may be and never covered entirely.
   */
  avoid: readonly Rect[];
  /** Other cutouts on the set (the rest of the cast): better not hidden. */
  others?: readonly Rect[];
  /**
   * The note's screen rectangle when it stands at stage x, `h` stage units
   * tall, at depth slot `d` (0 the nearest the set; later slots stand
   * further toward the reader), written into `out`. The note stands on the
   * page, so this is the whole of the camera and the page as far as
   * placement needs.
   */
  rectAt(x: number, h: number, d: number, out: Rect): Rect;
  /** How many depth slots there are. Default 1. */
  depths?: number;
  /** What standing in each slot costs, against the rest of the score (default 0.12 per slot). */
  slotCost?: readonly number[];
  /** Stage x of the target's centre, and the range the note's centre may stand in. */
  targetX: number;
  xMin: number;
  xMax: number;
  /** The note's wanted screen height (px), and the smallest it may shrink to. */
  wantPx: number;
  minPx: number;
  /** Physical limits on its height, stage units. */
  maxH: number;
  /** Where it stood before (stage x), to keep it from hopping as the camera eases; null for none. */
  prevX: number | null;
  /** The gap (px) it likes between itself and the target, room for the thread to show. Default 28. */
  gap?: number;
}

export interface Placement {
  /** Stage x of the note's centre, its height (stage units) and its depth slot. */
  x: number;
  h: number;
  depth: number;
  /** Which side of the target it stands: -1 left, 1 right. */
  side: -1 | 1;
  /** Entirely inside the view (false: the least-bad spot, shrunk as far as allowed). */
  fits: boolean;
  /** Its screen rectangle there. */
  rect: Rect;
}

export const placement = (): Placement => ({ x: 0, h: 0, depth: 0, side: 1, fits: false, rect: rect() });

/** Candidate spots across the stage, per height and depth tried. */
const SAMPLES = 40;
const SHRINK = 0.88;
const PASSES = 8;

/** Any overlap with an avoided rect up to this many px² still counts as clear (an antialiased corner). */
const CLEAR_SLACK = 24;

/**
 * Where the note stands: beside its target, wholly on screen, clear of the
 * caption (never over all of it, when nothing else fits), and as large as
 * wanted while that keeps it off the target and the rest of the cast
 * (smaller, toward minPx, or in a costlier slot, when that hides less of
 * them). Writes into `out` and allocates nothing, so it can run on every
 * frame the camera moves.
 */
export function placeNote(p: PlaceInput, out: Placement): Placement {
  // First wholly clear of the caption; only if no spot is, the least over it.
  if (p.avoid.length > 0 && searchNote(p, out, true)) return out;
  searchNote(p, out, false);
  return out;
}

/** One pass of placeNote's search, `clear` of the avoided rects or not; true when a spot fits. */
function searchNote(p: PlaceInput, out: Placement, clear: boolean): boolean {
  const tw = p.target.x1 - p.target.x0;
  const th = p.target.y1 - p.target.y0;
  const tArea = Math.max(1, tw * th);
  const tcx = (p.target.x0 + p.target.x1) / 2;
  const tcy = (p.target.y0 + p.target.y1) / 2;
  const vw = Math.max(1, p.view.x1 - p.view.x0);
  const gap = p.gap ?? 28;
  const depths = Math.max(1, p.depths ?? 1);
  const others = p.others ?? [];
  const r = out.rect;
  // Screen px per stage unit of height, where the target stands.
  p.rectAt(p.targetX, 1, 0, r);
  const perUnit = Math.max(1e-6, r.y1 - r.y0);
  const hWant = Math.min(p.maxH, p.wantPx / perUnit);
  const hMin = Math.min(hWant, p.minPx / perUnit);

  let bestScore = Infinity;
  let bestX = p.targetX;
  let bestH = hWant;
  let bestD = 0;
  let bestOut = Infinity;
  let bestOutX = p.targetX;
  let bestOutH = hMin;
  let bestOutD = 0;
  let h = hWant;
  for (let pass = 0; pass < PASSES; pass++) {
    // A smaller note reads less well: shrinking costs a little.
    const small = 1.4 * (1 - h / hWant);
    if (small >= bestScore) break;
    for (let d = 0; d < depths; d++) {
      for (let i = 0; i <= SAMPLES; i++) {
        const x = p.xMin + ((p.xMax - p.xMin) * i) / SAMPLES;
        p.rectAt(x, h, d, r);
        const off = outside(r, p.view);
        if (off > 0) {
          // Remember the least-outside spot, for when nothing fits.
          const o = off + Math.abs(x - p.targetX) * 1e-3 + d;
          if (o < bestOut) {
            bestOut = o;
            bestOutX = x;
            bestOutH = h;
            bestOutD = d;
          }
          continue;
        }
        let covered = false;
        let avoidCost = 0;
        for (const a of p.avoid) {
          const o = overlap(r, a);
          const k = o / Math.max(1, area(a));
          if (k > 0.97 || (clear && o > CLEAR_SLACK)) covered = true;
          avoidCost += k;
        }
        if (covered) continue;
        let crowd = 0;
        for (const o of others) crowd += overlap(r, o) / Math.max(1, area(o));
        const hide = overlap(r, p.target) / tArea;
        const cx = (r.x0 + r.x1) / 2;
        const cy = (r.y0 + r.y1) / 2;
        // Close beside it (a thread's length away), at about its height; off it above all.
        const gapX = Math.max(0, Math.abs(cx - tcx) - (r.x1 - r.x0) / 2 - tw / 2);
        const dist = (Math.abs(gapX - gap) + 0.35 * Math.abs(cy - tcy)) / vw;
        const hop = p.prevX === null ? 0 : Math.abs(x - p.prevX) * 0.4;
        const slot = p.slotCost?.[d] ?? 0.12 * d;
        const score = 6 * hide + 2.5 * crowd + 2 * avoidCost + dist + hop + small + slot;
        if (score < bestScore) {
          bestScore = score;
          bestX = x;
          bestH = h;
          bestD = d;
        }
      }
    }
    if (h <= hMin + 1e-9) break;
    h = Math.max(hMin, h * SHRINK);
  }
  const fits = bestScore < Infinity;
  if (clear && !fits) return false;
  out.x = fits ? bestX : bestOutX;
  out.h = fits ? bestH : bestOutH;
  out.depth = fits ? bestD : bestOutD;
  out.fits = fits;
  out.side = out.x >= p.targetX ? 1 : -1;
  p.rectAt(out.x, out.h, out.depth, r);
  return fits;
}

/* ------------------------------------------------------------------------ */
/* Which note is open                                                        */
/* ------------------------------------------------------------------------ */

export type ExplainEvent =
  /** A click (or tap) on an explainable cutout. */
  | { type: "target"; target: string }
  /** A click on the open note's card. */
  | { type: "note" }
  /** A click anywhere else on the book. */
  | { type: "elsewhere" }
  | { type: "escape" }
  /** The page started turning away. */
  | { type: "turn" }
  /** Another telling was chosen (or the scene was started again from outside). */
  | { type: "telling" }
  /** The open note's target left the set, or the stage went away. */
  | { type: "gone" }
  /** The test hook, or keyboard UI, asked for a target (null: close). */
  | { type: "open"; target: string | null };

export type CloseReason = Exclude<ExplainEvent["type"], "open" | "target"> | "toggle" | "probe";

export interface ExplainStep {
  /** The target open after the event, or null. */
  open: string | null;
  /** Why the note closed (null when one is open after, or nothing was). */
  closed: CloseReason | null;
}

/**
 * What a click, key or turn does to the open note. Clicking a target opens
 * its note, clicking it again closes it, and another target swaps notes;
 * anything else closes it. With no note open nothing but a target opens one.
 * `can` says whether a target may open now (on the set, explainable).
 */
export function explainStep(open: string | null, ev: ExplainEvent, can: (target: string) => boolean = () => true): ExplainStep {
  switch (ev.type) {
    case "target":
      if (ev.target === open) return { open: null, closed: "toggle" };
      if (!can(ev.target)) return { open, closed: null };
      return { open: ev.target, closed: null };
    case "open":
      if (ev.target === null) return { open: null, closed: open ? "probe" : null };
      if (!can(ev.target)) return { open, closed: null };
      return { open: ev.target, closed: null };
    default:
      return { open: null, closed: open ? ev.type : null };
  }
}

/**
 * Whether closing a note hands the scene back to play (when it was playing
 * as the note opened): yes for the reader putting the note away; no when
 * the page turned (the scene re-arms) or a telling was chosen (it plays
 * itself from the start).
 */
export function resumesAfter(reason: CloseReason): boolean {
  return reason !== "turn" && reason !== "telling";
}

/* ------------------------------------------------------------------------ */
/* Motion                                                                    */
/* ------------------------------------------------------------------------ */

/** How long the note takes to rise and settle, and to fold back down (ms). */
export const RISE_MS = 820;
export const FOLD_MS = 360;
/**
 * The cutout's lift: how long it takes, how far it comes toward the reader
 * (along the line to the camera, so it stays under the pointer, grows a
 * touch and its shadow on the set falls further off) and how far it also
 * rises, stage units.
 */
export const LIFT_MS = 220;
export const LIFT = 0.07;
export const LIFT_RISE = 0.014;
/** A hovered cutout lifts this fraction of the way. */
export const HOVER_LIFT = 0.65;
/** How much a fully lifted cutout brightens, as if it rose into the lamp (a fraction of its own colour). */
export const LIFT_GLOW = 0.22;

const clamp01 = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t);
export const smooth = (t: number) => {
  const u = clamp01(t);
  return u * u * (3 - 2 * u);
};

/**
 * How far up the note stands `ms` into its rise: 0 flat on the page, 1
 * standing. Paper on a hinge: quick off the page, past upright by a few
 * degrees, one soft bounce back, then still (exactly 1 from RISE_MS on).
 */
export function noteRise(ms: number): number {
  if (ms <= 0) return 0;
  if (ms >= RISE_MS) return 1;
  const t = ms / RISE_MS;
  // Up in the first 45%, eased out; then a damped wobble about standing.
  const up = 0.45;
  if (t < up) {
    const u = t / up;
    return 1.06 * (1 - (1 - u) * (1 - u) * (1 - u));
  }
  const u = (t - up) / (1 - up);
  return 1 + 0.06 * Math.cos(u * Math.PI * 1.5) * Math.pow(1 - u, 2);
}

/** How far up the note still stands `ms` into folding back down (1 standing .. 0 flat). */
export function noteFold(ms: number): number {
  if (ms <= 0) return 1;
  if (ms >= FOLD_MS) return 0;
  const u = ms / FOLD_MS;
  // A little lift off its lean, then down: accelerating, as paper falls.
  return Math.max(0, 1 + 0.04 * Math.sin(u * Math.PI) - u * u);
}

/**
 * How much of the thread is sewn (0..1) `ms` into the rise, or (folding)
 * how much is left: it is sewn from the cutout to the card once the card
 * is half up, and unpicked first when it folds.
 */
export function sewAt(ms: number, folding: boolean): number {
  if (folding) return 1 - clamp01(ms / (FOLD_MS * 0.45));
  return smooth((ms - RISE_MS * 0.28) / (RISE_MS * 0.55));
}

/** The cutout's lift, eased toward `goal` (0..1) by `dt` ms: [value, still moving]. */
export function stepLift(value: number, goal: number, dt: number): number {
  const k = 1 - Math.exp(-Math.max(0, dt) / (LIFT_MS / 3.5));
  const v = value + (goal - value) * k;
  return Math.abs(goal - v) < 0.002 ? goal : v;
}
