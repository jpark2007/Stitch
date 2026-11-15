// "New" marks for the demo control page's live lists (moments and films):
// whatever was not in the first list the page loaded is new, and new items
// float to the top, the latest arrival first, so a story that just came in
// from a text or a call is the first thing the operator sees.
//
// No runtime imports and only erasable TypeScript, so `node --test` loads this
// file directly. Tests: components/demo/__tests__/arrivals.test.mjs.

export interface Arrivals {
  /** Ids in the first list loaded; null until it has loaded. */
  baseline: ReadonlySet<string> | null;
  /** Id -> when it was first seen (ms), for every id not in the baseline. */
  arrived: ReadonlyMap<string, number>;
}

export const NO_ARRIVALS: Arrivals = { baseline: null, arrived: new Map() };

/**
 * Fold one poll in. The first list becomes the baseline (nothing in it is
 * new). After that, an id not in the baseline gets its arrival time, kept
 * across polls. Returns `prev` itself when nothing changed.
 */
export function noteArrivals(prev: Arrivals, ids: readonly string[], now: number): Arrivals {
  if (prev.baseline === null) return { baseline: new Set(ids), arrived: new Map() };
  let arrived: Map<string, number> | null = null;
  for (const id of ids) {
    if (prev.baseline.has(id) || prev.arrived.has(id) || arrived?.has(id)) continue;
    arrived ??= new Map(prev.arrived);
    arrived.set(id, now);
  }
  return arrived ? { baseline: prev.baseline, arrived } : prev;
}

/** True when `id` arrived after the page loaded. */
export function isNew(a: Arrivals, id: string): boolean {
  return a.arrived.has(id);
}

/**
 * The items in display order: new ones first (latest arrival first), then
 * the rest in `compare` order. Does not change `items`.
 */
export function withNewFirst<T extends { id: string }>(
  items: readonly T[],
  a: Arrivals,
  compare: (x: T, y: T) => number,
): T[] {
  const fresh = items.filter((m) => a.arrived.has(m.id));
  const rest = items.filter((m) => !a.arrived.has(m.id));
  fresh.sort((x, y) => (a.arrived.get(y.id) ?? 0) - (a.arrived.get(x.id) ?? 0) || compare(x, y));
  rest.sort(compare);
  return [...fresh, ...rest];
}

/** Moments: the latest year first, then by title. */
export function byYearDesc(x: { year: number; title: string }, y: { year: number; title: string }): number {
  return y.year - x.year || x.title.localeCompare(y.title);
}
