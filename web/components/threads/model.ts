/**
 * Threads layout model: pure, no DOM.
 *
 * Turns people + moments into lanes, knots and a y(t) function per person.
 * Time is in years (floats); y is in "lane units" (0 = top lane), so the
 * renderer can scale both independently while zooming.
 *
 * Shapes are kept close to ARCHITECTURE.md section 5 (`people`, `memories`)
 * and trimmed to what this screen reads, so they can be swapped for
 * `web/lib/types.ts` later.
 */

export interface ThreadPerson {
  id: string;
  name: string;
  born: number;
  parentIds: string[];
  spouseIds: string[];
  /** Thread colour (person.character.color), hex. */
  color: string;
}

export interface ThreadMoment {
  id: string;
  title: string;
  year: number;
  participantIds: string[];
}

/** A point the thread must pass through: a knot, or where it is born. */
interface Anchor {
  t: number;
  y: number;
  /** Birth anchor: y follows the midpoint of these parents' threads. */
  parents?: Strand[];
}

export interface Strand {
  person: ThreadPerson;
  /** Index into `lanes`; the thread rests here between moments. */
  lane: number;
  /** Where the thread starts (birth) and its y there. */
  start: Anchor;
  /** True when the thread is spun out of its parents' threads. */
  fromParents: boolean;
  anchors: Anchor[];
  /** Moment ids this person is in, oldest first. */
  momentIds: string[];
}

export interface Knot {
  moment: ThreadMoment;
  t: number;
  y: number;
  /** Participants in lane order, so loops fan out top to bottom. */
  people: ThreadPerson[];
}

export interface ThreadsModel {
  strands: Strand[];
  /** Same strands, parents before children. */
  birthOrder: Strand[];
  knots: Knot[];
  laneCount: number;
  /** Time range worth showing (years). */
  tMin: number;
  tMax: number;
  /** Where every thread currently ends ("now"). */
  tNow: number;
}

/* ------------------------------------------------------------------ */
/* Lane order                                                          */
/* ------------------------------------------------------------------ */

/**
 * Order people top to bottom so that people who share moments, spouses and
 * parent/child sit close together. Cost = spread of every moment's lanes +
 * a pull towards family. Hill-climbs pairwise swaps from a family-first
 * seed; deterministic and fine for families of any realistic size.
 */
function orderLanes(people: ThreadPerson[], moments: ThreadMoment[]): ThreadPerson[] {
  const ids = people.map((p) => p.id);
  const idx = new Map(ids.map((id, i) => [id, i]));
  const groups: number[][] = moments
    .map((m) => m.participantIds.map((id) => idx.get(id)).filter((i): i is number => i !== undefined))
    .filter((g) => g.length > 1);
  const pairs: [number, number, number][] = [];
  for (const p of people) {
    const a = idx.get(p.id)!;
    for (const s of p.spouseIds) {
      const b = idx.get(s);
      if (b !== undefined && b > a) pairs.push([a, b, 1.2]);
    }
    for (const par of p.parentIds) {
      const b = idx.get(par);
      if (b !== undefined) pairs.push([a, b, 0.35]);
    }
  }

  const cost = (pos: number[]) => {
    let c = 0;
    for (const g of groups) {
      let mean = 0;
      for (const i of g) mean += pos[i];
      mean /= g.length;
      for (const i of g) c += (pos[i] - mean) ** 2;
    }
    for (const [a, b, w] of pairs) c += w * (pos[a] - pos[b]) ** 2;
    return c;
  };

  // Seed: oldest first, which keeps generations roughly banded.
  const seed = people
    .map((p, i) => ({ p, i }))
    .sort((a, b) => a.p.born - b.p.born || a.i - b.i)
    .map((x) => x.i);
  const pos = new Array<number>(people.length);
  seed.forEach((personIdx, lane) => (pos[personIdx] = lane));

  let best = cost(pos);
  for (let iter = 0; iter < 60; iter++) {
    let improved = false;
    for (let a = 0; a < people.length; a++) {
      for (let b = a + 1; b < people.length; b++) {
        [pos[a], pos[b]] = [pos[b], pos[a]];
        const c = cost(pos);
        if (c < best - 1e-9) {
          best = c;
          improved = true;
        } else {
          [pos[a], pos[b]] = [pos[b], pos[a]];
        }
      }
    }
    if (!improved) break;
  }
  const ordered = new Array<ThreadPerson>(people.length);
  people.forEach((p, i) => (ordered[pos[i]] = p));
  return ordered;
}

/* ------------------------------------------------------------------ */
/* Thread shape                                                        */
/* ------------------------------------------------------------------ */

/**
 * y(t) for a strand: Shepard interpolation between its knots, relaxing to
 * its lane away from them. It passes exactly through every knot, and flat
 * (zero slope) through each, so threads run level through a knot like
 * real thread pulled tight. `sigma` (years) is how far a knot's pull
 * reaches; the renderer ties it to zoom so the cloth relaxes as you zoom
 * in.
 */
export function strandY(s: Strand, t: number, sigma: number): number {
  const P = 3;
  const wLane = 1 / Math.pow(sigma, P);
  let num = s.lane * wLane;
  let den = wLane;
  for (let i = 0; i < s.anchors.length; i++) {
    const a = s.anchors[i];
    const d = Math.abs(t - a.t);
    if (d < 1e-6) return a.y;
    // Only nearby knots matter; skip far ones cheaply.
    if (d > sigma * 8) continue;
    const w = 1 / (d * d * d);
    num += a.y * w;
    den += w;
  }
  return num / den;
}

/* ------------------------------------------------------------------ */
/* Build                                                               */
/* ------------------------------------------------------------------ */

export function buildModel(people: ThreadPerson[], moments: ThreadMoment[], now: number): ThreadsModel {
  const ordered = orderLanes(people, moments);
  const laneOf = new Map(ordered.map((p, i) => [p.id, i]));
  const byId = new Map(people.map((p) => [p.id, p]));

  const sorted = [...moments]
    .filter((m) => m.participantIds.some((id) => laneOf.has(id)))
    .sort((a, b) => a.year - b.year || a.id.localeCompare(b.id));

  const knots: Knot[] = sorted.map((m) => {
    const ps = m.participantIds
      .map((id) => byId.get(id))
      .filter((p): p is ThreadPerson => !!p)
      .sort((a, b) => laneOf.get(a.id)! - laneOf.get(b.id)!);
    const y = ps.reduce((acc, p) => acc + laneOf.get(p.id)!, 0) / ps.length;
    // Moments in the same year are nudged apart a little so knots never sit
    // on top of each other.
    return { moment: m, t: m.year + 0.5, y, people: ps };
  });
  // Spread same-year knots within their year.
  const byYear = new Map<number, Knot[]>();
  for (const k of knots) {
    const list = byYear.get(k.moment.year) ?? [];
    list.push(k);
    byYear.set(k.moment.year, list);
  }
  for (const list of byYear.values()) {
    if (list.length < 2) continue;
    list.forEach((k, i) => (k.t = k.moment.year + (i + 1) / (list.length + 1)));
  }

  // Strands, parents first so a child can be spun from their threads.
  const strands = new Map<string, Strand>();
  const build = (p: ThreadPerson): Strand => {
    const existing = strands.get(p.id);
    if (existing) return existing;
    const lane = laneOf.get(p.id)!;
    const mine = knots.filter((k) => k.people.some((q) => q.id === p.id));
    const anchors: Anchor[] = mine.map((k) => ({ t: k.t, y: k.y }));
    const parents = p.parentIds.map((id) => byId.get(id)).filter((q): q is ThreadPerson => !!q && laneOf.has(q.id));
    let start: Anchor = { t: p.born, y: lane };
    let fromParents = false;
    const firstKnot = mine[0];
    if (firstKnot && firstKnot.t - p.born <= 1) {
      // Born into a moment ("Sofia comes home"): the thread starts at it.
      start = { t: firstKnot.t, y: firstKnot.y };
      fromParents = parents.length > 0;
    } else if (parents.length > 0) {
      start = { t: p.born, y: lane, parents: parents.map(build) };
      fromParents = true;
      anchors.unshift(start);
    }
    const s: Strand = { person: p, lane, start, fromParents, anchors, momentIds: mine.map((k) => k.moment.id) };
    strands.set(p.id, s);
    return s;
  };
  ordered.forEach(build);

  const out = ordered.map((p) => strands.get(p.id)!);
  // Parents before children, for relax().
  const depth = (s: Strand): number =>
    s.start.parents ? 1 + Math.max(...s.start.parents.map(depth)) : 0;
  const birthOrder = [...out].sort((a, b) => depth(a) - depth(b));

  const born = Math.min(...people.map((p) => p.born));
  const last = Math.max(now, ...sorted.map((m) => m.year + 1));
  return {
    strands: out,
    birthOrder,
    knots,
    laneCount: ordered.length,
    tMin: Math.floor(born / 5) * 5 - 3,
    tMax: last + 3,
    tNow: last,
  };
}

/**
 * Re-solve birth points for the current `sigma`: a child's thread starts
 * exactly between its parents' threads as they are drawn right now.
 */
export function relax(model: ThreadsModel, sigma: number) {
  for (const s of model.birthOrder) {
    const ps = s.start.parents;
    if (!ps) continue;
    let y = 0;
    for (const q of ps) y += strandY(q, s.start.t, sigma);
    s.start.y = y / ps.length;
  }
}
