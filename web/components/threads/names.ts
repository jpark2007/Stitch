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
 *
 * A name also keeps its place: given where it was written last frame
 * (`held`, already moved with the pan), it takes the free spot nearest
 * there, so while the view pans it stays put on its thread (or rides the
 * margin) rather than hopping between spots. It moves to another spot only
 * once its own is taken or gone.
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
  /**
   * Where this name's left edge was last frame, in this frame's px (moved
   * with the pan); absent or NaN when it was not written. The free spot
   * nearest it is taken over the best one, when within a name's width.
   */
  held?: number;
  /**
   * With `held`: the baseline for a clean spot with the name's left edge at
   * x (over level thread, clear of knots and other threads), NaN where there
   * is none. Lets the name stay exactly where it was, or be nudged along.
   */
  fit?: (x: number) => number;
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
  /** The first name already placed that a box clashes with, or null. */
  const clashWith = (b: NameBox) => {
    for (let i = 0; i < placed.length; i++) if (namesClash(placed[i], b)) return placed[i];
    return null;
  };
  /**
   * Holding a place: the name stays where it was, held inside the gutter (so
   * it rides the margin) and nudged along by a name in its way (so it rides
   * behind that one), never further than its own width. Null when it cannot.
   */
  const keep = (r: NameRequest, held: number, fit: (x: number) => number): NameSpot | null => {
    let x = held;
    let dir = 0;
    for (let tries = 0; tries < 4; tries++) {
      x = Math.max(NAME_GUTTER, Math.min(W - NAME_GUTTER - r.width, x));
      if (Math.abs(x - held) > r.width) return null;
      const y = fit(x);
      if (!Number.isFinite(y)) return null;
      const c = clashWith(nameBox(x, y, r.width, size));
      if (!c) return { x, y };
      // Pushed away from it, one way only.
      const d = c.x0 + c.x1 < 2 * x + r.width ? 1 : -1;
      if (dir && d !== dir) return null;
      dir = d;
      x = d > 0 ? c.x1 + PAD_X + 1e-3 : c.x0 - PAD_X - r.width - 1e-3;
    }
    return null;
  };
  const put = (r: NameRequest, sp: NameSpot, forced = false) => {
    placed.push({ ...nameBox(sp.x, sp.y, r.width, size), strand: r.strand, x: sp.x, y: sp.y, forced });
  };
  /** Holding a place and able to stay there (or be nudged along): placed. */
  const stay = (r: NameRequest) => {
    const held = r.held ?? NaN;
    const sp = Number.isFinite(held) && r.fit ? keep(r, held, r.fit) : null;
    if (sp) put(r, sp);
    return !!sp;
  };
  /** The free spot nearest where it was (within a name's width), else the best free spot. */
  const move = (r: NameRequest) => {
    const held = r.held ?? NaN;
    const holding = Number.isFinite(held);
    let best: NameSpot | null = null;
    let near: NameSpot | null = null;
    let nearD = r.width;
    for (const sp of r.spots) {
      if (sp.forced) continue;
      const d = holding ? Math.abs(sp.x - held) : Infinity;
      if (best && !(d < nearD)) continue;
      const box = nameBox(sp.x, sp.y, r.width, size);
      if (!insideGutter(box, W) || clashWith(box)) continue;
      best ??= sp;
      if (d < nearD) {
        near = sp;
        nearD = d;
      }
      if (!holding || d === 0) break;
    }
    const pick = near ?? best;
    if (pick) put(r, pick);
    else if (r.pinned) {
      // The followed name goes first, so nothing is in its way: take the first
      // spot (clean or forced), held inside the gutter.
      const sp = r.spots.find((s) => s.forced) ?? r.spots[0];
      if (sp) put(r, { x: Math.max(NAME_GUTTER, Math.min(W - NAME_GUTTER - r.width, sp.x)), y: sp.y }, true);
    }
  };
  // The followed name first. Then every name that can stay where it was
  // does, before any that must move, so a name that has to find a new spot
  // never pushes out one that need not. Then the rest, by priority.
  let i0 = 0;
  if (order.length && order[0].pinned) {
    if (!stay(order[0])) move(order[0]);
    i0 = 1;
  }
  const kept = new Uint8Array(order.length);
  for (let i = i0; i < order.length; i++) kept[i] = stay(order[i]) ? 1 : 0;
  for (let i = i0; i < order.length; i++) if (!kept[i]) move(order[i]);
  if (shown) {
    shown.fill(0);
    for (const p of placed) shown[p.strand] = 1;
  }
  return placed;
}
