/**
 * Where person names go, frame by frame: pure, no canvas.
 *
 * The renderer offers each thread a few spots where its name could be
 * written (over a level stretch of its own thread, clear of knots and other
 * threads), best first. This step picks at most one per thread so that:
 *
 *  - no two names touch: a name whose spots are all taken slides to its
 *    next spot further along its thread, so crowded lanes stagger instead
 *    of stacking, and failing that it is left out for this frame;
 *  - every name stays inside the 16px gutter on both sides;
 *  - the followed person's name is always written (placed first, and on a
 *    forced spot when no clean one is left);
 *  - text never shrinks below legibility to make room: names that do not
 *    fit are dropped, lowest priority first.
 *
 * Priority is the caller's `rank` (lit, hovered, how many moments...), with
 * a small bonus for names shown last frame so they do not flicker while
 * panning.
 */

/** Side gutter (px) no name may enter, as in the page's --gutter. */
export const NAME_GUTTER = 16;
/** Smallest name size (px) the renderer may use. */
export const NAME_MIN_PX = 14;
/** Clear space kept between two names: across, and between rows. */
const PAD_X = 12;
const PAD_Y = 1;
/** How much priority a name shown last frame keeps, so close calls do not flicker. */
const STICKY = 2.5;

export interface NameSpot {
  /** Left edge and baseline (px). */
  x: number;
  y: number;
  /** A last resort that ignores threads and knots: only the followed person's name uses it. */
  forced?: boolean;
}

export interface NameRequest {
  strand: number;
  /** Measured width (px). */
  width: number;
  /** Candidate spots, best first. */
  spots: NameSpot[];
  /** Higher goes first. */
  rank: number;
  /** The followed person: always written, before everyone else. */
  pinned?: boolean;
}

export interface NameBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface PlacedName extends NameBox {
  strand: number;
  /** Left edge and baseline (px). */
  x: number;
  y: number;
  forced: boolean;
}

export interface NameLayoutOptions {
  /** View width (px). */
  width: number;
  /** Font size (px). */
  size: number;
  /** Per strand: 1 when its name was shown last frame. Updated in place. */
  shown?: Uint8Array;
}

/** The box a name of width `w` written at (x, baseline y) covers, descenders included. */
export function nameBox(x: number, y: number, w: number, size: number): NameBox {
  return { x0: x, y0: y - size, x1: x + w, y1: y + Math.round(size * 0.25) };
}

/** True when two name boxes are closer than the clear space names keep. */
export function namesClash(a: NameBox, b: NameBox): boolean {
  return a.x0 < b.x1 + PAD_X && b.x0 < a.x1 + PAD_X && a.y0 < b.y1 + PAD_Y && b.y0 < a.y1 + PAD_Y;
}

/** True when a box sits wholly inside the view, gutters excluded. */
export function insideGutter(b: NameBox, width: number): boolean {
  return b.x0 >= NAME_GUTTER - 1e-6 && b.x1 <= width - NAME_GUTTER + 1e-6;
}

export function placeNames(requests: NameRequest[], o: NameLayoutOptions): PlacedName[] {
  const { width: W, size, shown } = o;
  const score = (r: NameRequest) => r.rank + (shown && shown[r.strand] ? STICKY : 0);
  const order = requests.slice().sort((a, b) => {
    if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
    return score(b) - score(a) || a.strand - b.strand;
  });
  const placed: PlacedName[] = [];
  const clashes = (b: NameBox) => {
    for (let i = 0; i < placed.length; i++) if (namesClash(placed[i], b)) return true;
    return false;
  };
  for (const r of order) {
    let hit: PlacedName | null = null;
    for (const sp of r.spots) {
      if (sp.forced) continue;
      const box = nameBox(sp.x, sp.y, r.width, size);
      if (!insideGutter(box, W) || clashes(box)) continue;
      hit = { ...box, strand: r.strand, x: sp.x, y: sp.y, forced: false };
      break;
    }
    if (!hit && r.pinned) {
      // The followed name goes first, so nothing is in its way: take the first
      // spot (clean or forced), held inside the gutter.
      const sp = r.spots.find((s) => s.forced) ?? r.spots[0];
      if (sp) {
        const x = Math.max(NAME_GUTTER, Math.min(W - NAME_GUTTER - r.width, sp.x));
        hit = { ...nameBox(x, sp.y, r.width, size), strand: r.strand, x, y: sp.y, forced: true };
      }
    }
    if (hit) placed.push(hit);
  }
  if (shown) {
    shown.fill(0);
    for (const p of placed) shown[p.strand] = 1;
  }
  return placed;
}
