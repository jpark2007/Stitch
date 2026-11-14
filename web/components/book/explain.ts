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
      out.push({ ...t, title: p?.name?.trim() || titleFromId(t.id), text, color: p?.color ?? null });
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
  /** Things the note may overlap but never cover entirely (the printed caption). */
  avoid: readonly Rect[];
  /**
   * The note's screen rectangle when it stands at stage x, `h` stage units
   * tall (written into `out`). The note stands on the page, so this is the
   * whole of the camera and the page as far as placement needs.
   */
  rectAt(x: number, h: number, out: Rect): Rect;
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
}

export interface Placement {
  /** Stage x of the note's centre, and its height (stage units). */
  x: number;
  h: number;
  /** Which side of the target it stands: -1 left, 1 right. */
  side: -1 | 1;
  /** Entirely inside the view (false: the least-bad spot, shrunk as far as allowed). */
  fits: boolean;
  /** Its screen rectangle there. */
  rect: Rect;
}

export const placement = (): Placement => ({ x: 0, h: 0, side: 1, fits: false, rect: rect() });

/** Candidate spots across the stage, per height tried. */
const SAMPLES = 40;
const SHRINK = 0.88;

/**
 * Where the note stands: beside its target, wholly on screen, not over the
 * target and never over all of the caption, as large as wanted when there
 * is room (shrinking toward minPx when there is not). Writes into `out` and
 * allocates nothing, so it can run on every frame the camera moves.
 */
export function placeNote(p: PlaceInput, out: Placement): Placement {
  const tw = p.target.x1 - p.target.x0;
  const th = p.target.y1 - p.target.y0;
  const tArea = Math.max(1, tw * th);
  const tcx = (p.target.x0 + p.target.x1) / 2;
  const tcy = (p.target.y0 + p.target.y1) / 2;
  const vw = Math.max(1, p.view.x1 - p.view.x0);
  const r = out.rect;
  // Screen px per stage unit of height, where the target stands.
  p.rectAt(p.targetX, 1, r);
  const perUnit = Math.max(1e-6, r.y1 - r.y0);
  let h = Math.min(p.maxH, p.wantPx / perUnit);
  const hMin = Math.min(h, p.minPx / perUnit);

  let bestScore = Infinity;
  let bestX = p.targetX;
  let bestH = h;
  let bestOut = Infinity;
  let bestOutX = p.targetX;
  let bestOutH = hMin;
  for (let pass = 0; pass < 12; pass++) {
    for (let i = 0; i <= SAMPLES; i++) {
      const x = p.xMin + ((p.xMax - p.xMin) * i) / SAMPLES;
      p.rectAt(x, h, r);
      const off = outside(r, p.view);
      if (off > 0) {
        // Remember the least-outside spot, for when nothing fits.
        const o = off + Math.abs(x - p.targetX) * 1e-3;
        if (o < bestOut) {
          bestOut = o;
          bestOutX = x;
          bestOutH = h;
        }
        continue;
      }
      let covered = false;
      let avoidCost = 0;
      for (const a of p.avoid) {
        const k = overlap(r, a) / Math.max(1, area(a));
        if (k > 0.97) covered = true;
        avoidCost += k;
      }
      if (covered) continue;
      const hide = overlap(r, p.target) / tArea;
      const cx = (r.x0 + r.x1) / 2;
      const cy = (r.y0 + r.y1) / 2;
      // Close beside it, at about its height; off it above all.
      const gapX = Math.max(0, Math.abs(cx - tcx) - (r.x1 - r.x0) / 2 - tw / 2);
      const dist = (gapX + 0.35 * Math.abs(cy - tcy)) / vw;
      const hop = p.prevX === null ? 0 : Math.abs(x - p.prevX) * 0.4;
      const score = 6 * hide + 1.2 * avoidCost + dist + hop;
      if (score < bestScore) {
        bestScore = score;
        bestX = x;
        bestH = h;
      }
    }
    if (bestScore < Infinity) break;
    if (h <= hMin + 1e-9) break;
    h = Math.max(hMin, h * SHRINK);
  }
  const fits = bestScore < Infinity;
  out.x = fits ? bestX : bestOutX;
  out.h = fits ? bestH : bestOutH;
  out.fits = fits;
  out.side = out.x >= p.targetX ? 1 : -1;
  p.rectAt(out.x, out.h, r);
  return out;
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
/** The cutout's lift: how long it takes, and its height off the layer (stage units). */
export const LIFT_MS = 220;
export const LIFT = 0.032;
/** A hovered cutout lifts this fraction of LIFT. */
export const HOVER_LIFT = 0.4;

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
