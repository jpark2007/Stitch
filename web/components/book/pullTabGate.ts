/**
 * When a pull tab is out of its slit, and when it takes a pull
 * (moment/PullTabLever.tsx runs it once per drawn frame).
 *
 *   const g = tabGate({ tabShot, frameShot, reveal, openAt: OPEN_TO_PLAY, held, away });
 *   away = stepTabAway(away, g.out, dt);   // 0 out of the slit .. 1 home in it
 *   if (g.grabbable) // take presses, hover, the grab cursor
 *   if (endNudge({ ended, grabbable: g.grabbable, pulled, held, nudged })) // wiggle once
 *
 * The rule: a tab is shown out of its slit only when it can be pulled; the
 * scene is never seeked by a tab (CONTRACTS 7).
 *
 *   - a tab bound to a shot is out only while that shot is on stage; after
 *     the telling the frame keeps the last shot, so the last shot's tabs stay
 *     out in the end hold and at rest;
 *   - a tab with no shot, or any tab before the first shot, is out;
 *   - a held tab stays out, so a shot changing under the hand never yanks it;
 *   - an out tab takes a pull from the moment the scene may play (the play
 *     gate's OPEN_TO_PLAY), once it has slid most of the way out.
 *
 * Pure, no imports (pullTabGate.test.ts).
 */

export interface TabGateInput {
  /** The tab's interaction's shot (null: every shot). */
  tabShot: string | null | undefined;
  /** The shot on stage now (null: before the first). */
  frameShot: string | null | undefined;
  /** The book's reveal for this spread (0 folded .. 1 open and still). */
  reveal: number;
  /** The reveal a pull is taken from (the play gate's OPEN_TO_PLAY). */
  openAt: number;
  /** Under the pointer's hand. */
  held: boolean;
  /** How far home in its slit the card is (0 out .. 1 home). */
  away: number;
}

export interface TabGate {
  /** The card's target: out of its slit, or home in it. */
  out: boolean;
  /** It takes a press (and hover, the grab cursor and nudges). */
  grabbable: boolean;
}

/** The tab belongs to the shot on stage (or to every shot). */
export function tabOnShot(tabShot: string | null | undefined, frameShot: string | null | undefined): boolean {
  return !tabShot || frameShot == null || frameShot === tabShot;
}

export function tabGate(i: TabGateInput): TabGate {
  const out = i.held || tabOnShot(i.tabShot, i.frameShot);
  return { out, grabbable: i.held || (out && i.reveal >= i.openAt && i.away < 0.5) };
}

/** One frame of the card sliding toward out (away 0) or home (away 1), `rate` per second, eased. */
export function stepTabAway(away: number, out: boolean, dt: number, rate = 4): number {
  const target = out ? 0 : 1;
  if (away === target) return away;
  const step = Math.min(Math.max(dt, 0), 0.05) * rate;
  // Ease: quick while far, gentle as it seats, with a floor so it arrives.
  const gap = target - away;
  const move = Math.sign(gap) * Math.min(Math.abs(gap), step * Math.max(0.35, Math.min(1, 2 * Math.abs(gap))));
  const next = away + move;
  return Math.abs(target - next) < 1e-3 ? target : next;
}

/**
 * A home tab's printed slit and label eat a press (and show the plain
 * cursor), so a click on them does nothing rather than turning the page:
 * while the page is open and the tab does not take a pull. While the page is
 * still opening or turning, a press falls through to the book.
 */
export function printEats(i: { grabbable: boolean; reveal: number; openAt: number }): boolean {
  return !i.grabbable && i.reveal >= i.openAt;
}

export interface PrintRect {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}

/** The pencilled label's hand, page px (labelArt.drawPullTabPrint): its size, a generous letter, its widest line. */
const LABEL = { size: 35, gap: 46, letter: 14, wrap: 360 } as const;

/**
 * The printed slit and label, in page px about the slit's middle (x along
 * the tab, y down the page), for a card `length` × `width` and its `label`:
 * the slit's cut and the strip's footprint, and the pencilled label under
 * it, right-aligned under the grip, up to its arrow. Nothing above the
 * slit's top, so the stage and its caption stay clear. The label's width is
 * estimated from its letters (it is drawn on the page canvas, not here),
 * generously, as labelArt wraps it: one line up to 360 px, else two.
 */
export function printRects(length: number, width: number, label: string): PrintRect[] {
  const end = length + 4;
  const words = label.trim();
  const full = words.length * LABEL.letter;
  const lines = full > LABEL.wrap ? 2 : 1;
  const wide = Math.min(LABEL.wrap + 40, lines === 2 ? Math.ceil(words.length / 2 + 3) * LABEL.letter : full);
  const base = width / 2 + LABEL.gap;
  return [
    { x0: -24, x1: end + 44, y0: -(width / 2 + 9), y1: width / 2 + 8 },
    { x0: end - wide - 12, x1: end + 48, y0: base - LABEL.size - 4, y1: base + (lines - 1) * LABEL.size + 14 },
  ];
}

/**
 * The grab area's margin round the card: this many screen px (the old fixed
 * 0.04 world gave ~50 px on a close desktop view but ~13 px on a phone).
 */
export const HIT_PAD_PX = 22;
/** ...but never under or over this, world units (the page is 1 wide, a card 0.15 × 0.076). */
export const HIT_PAD_MIN = 0.02;
export const HIT_PAD_MAX = 0.07;

/**
 * The grab area's margin, world units, for a view showing `worldPerPx` world
 * units per CSS px at the tab: a steady HIT_PAD_PX on screen, clamped so a
 * far view does not take a slab of the page-turn band and a close one still
 * forgives a press just off the card.
 */
export function hitPad(worldPerPx: number): number {
  if (!(worldPerPx > 0) || !Number.isFinite(worldPerPx)) return HIT_PAD_MIN;
  return Math.min(HIT_PAD_MAX, Math.max(HIT_PAD_MIN, HIT_PAD_PX * worldPerPx));
}

/** World units per CSS px at `distance` from a perspective camera of vertical `fovDeg`, on a view `heightPx` tall. */
export function worldPerPx(distance: number, fovDeg: number, heightPx: number): number {
  return (2 * distance * Math.tan((fovDeg * Math.PI) / 360)) / Math.max(1, heightPx);
}

/** The end-of-telling nudge: once, for a tab that can be pulled and has not been. */
export function endNudge(i: { ended: boolean; grabbable: boolean; pulled: boolean; held: boolean; nudged: boolean }): boolean {
  return i.ended && i.grabbable && !i.pulled && !i.held && !i.nudged;
}
