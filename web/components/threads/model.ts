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
  /** Where the thread ends ("now"); it settles back on its lane by then. */
  end: number;
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

  const n = people.length;
  const posOf = (order: number[]) => {
    const pos = new Array<number>(n);
    order.forEach((personIdx, lane) => (pos[personIdx] = lane));
    return pos;
  };
  const costOf = (order: number[]) => cost(posOf(order));

  // Local search over lane orders with swap and move-to moves, from a few
  // deterministic seeds; keep the best.
  const climb = (start: number[]) => {
    let order = start.slice();
    let best = costOf(order);
    for (let iter = 0; iter < 40; iter++) {
      let improved = false;
      for (let a = 0; a < n; a++) {
        for (let b = 0; b < n; b++) {
          if (a === b) continue;
          // Swap.
          if (a < b) {
            const sw = order.slice();
            [sw[a], sw[b]] = [sw[b], sw[a]];
            const c = costOf(sw);
            if (c < best - 1e-9) {
              best = c;
              order = sw;
              improved = true;
            }
          }
          // Move a to b.
          const mv = order.slice();
          const [x] = mv.splice(a, 1);
          mv.splice(b, 0, x);
          const c = costOf(mv);
          if (c < best - 1e-9) {
            best = c;
            order = mv;
            improved = true;
          }
        }
      }
      if (!improved) break;
    }
    return { order, best };
  };

  const byBorn = people
    .map((p, i) => ({ p, i }))
    .sort((a, b) => a.p.born - b.p.born || a.i - b.i)
    .map((x) => x.i);
  const seeds = [byBorn, [...byBorn].reverse(), people.map((_, i) => i)];
  let winner = climb(seeds[0]);
  for (const s of seeds.slice(1)) {
    const r = climb(s);
    if (r.best < winner.best - 1e-9) winner = r;
  }
  // Oldest generation on top reads most naturally; flip if it ended low.
  const pos = posOf(winner.order);
  const mean = (pick: (p: ThreadPerson) => boolean) => {
    const ls = people.map((p, i) => (pick(p) ? pos[i] : -1)).filter((l) => l >= 0);
    return ls.reduce((a, b) => a + b, 0) / Math.max(1, ls.length);
  };
  const order = mean((p) => p.parentIds.length === 0) > mean((p) => p.parentIds.length > 0)
    ? [...winner.order].reverse()
    : winner.order;
  return order.map((i) => people[i]);
}

/* ------------------------------------------------------------------ */
/* Thread shape                                                        */
/* ------------------------------------------------------------------ */

/** How the current zoom maps model units to pixels; threads bend in px. */
export interface Shape {
  /** Pixels per year. */
  s: number;
  /** Pixels between lanes. */
  gap: number;
}

/** Years a thread takes to swing `dLanes` lanes: longer for bigger swings. */
function swing(dLanes: number, sh: Shape): number {
  const px = 30 + Math.abs(dLanes) * sh.gap * 0.62;
  return Math.min(7, Math.max(0.22, px / sh.s));
}

function smoother(x: number): number {
  const u = x < 0 ? 0 : x > 1 ? 1 : x;
  return u * u * u * (u * (u * 6 - 15) + 10);
}

/** Where a thread rests between knots a and b, and how long each swing takes. */
function between(a: Anchor, b: Anchor, lane: number, sh: Shape) {
  const D = b.t - a.t;
  const L1 = swing(a.y - lane, sh);
  const L2 = swing(b.y - lane, sh);
  const f = Math.min(1, D / (L1 + L2));
  const mid = (a.y + b.y) / 2;
  return { D, rest: mid + (lane - mid) * f * f, l1: L1 * f, l2: L2 * f };
}

export interface StrandSample {
  /** y in lanes. */
  y: number;
  /** Vertical distance travelled since the thread began, in lanes. */
  travel: number;
}

const sample: StrandSample = { y: 0, travel: 0 };

/**
 * The thread at time t. It rests on its lane, swings to each knot in an
 * S-curve, runs level through it (like thread pulled tight) and swings
 * back. When two knots are too close to go home in between, it only dips
 * part of the way, so nothing pops as you zoom. Every swing is monotonic,
 * so `travel` (used to space stitches along steep parts) is exact.
 *
 * Returns a shared object: read it before the next call.
 */
export function strandAt(st: Strand, t: number, sh: Shape): StrandSample {
  const A = st.anchors;
  const lane = st.lane;
  const n = A.length;
  sample.travel = 0;
  if (n === 0) {
    sample.y = lane;
    return sample;
  }
  const first = A[0];
  const before = first.t - st.start.t; // > 0 when the thread starts on its lane
  const Lp = before > 0 ? Math.max(0.05, Math.min(swing(first.y - lane, sh), before * 0.9)) : 1;
  const preRise = before > 0 ? Math.abs(first.y - lane) : 0;
  if (t <= first.t) {
    const k = before > 0 ? smoother(1 - (first.t - t) / Lp) : 1;
    sample.y = lane + (first.y - lane) * k;
    sample.travel = preRise * k;
    return sample;
  }
  let travel = preRise;
  let i = 0;
  while (i < n - 1 && A[i + 1].t <= t) {
    const seg = between(A[i], A[i + 1], lane, sh);
    travel += Math.abs(seg.rest - A[i].y) + Math.abs(A[i + 1].y - seg.rest);
    i++;
  }
  if (i === n - 1) {
    const last = A[n - 1];
    const after = st.end - last.t;
    const L = Math.max(0.05, Math.min(swing(last.y - lane, sh), after * 0.9));
    const k = smoother((t - last.t) / L);
    sample.y = last.y + (lane - last.y) * k;
    sample.travel = travel + Math.abs(lane - last.y) * k;
    return sample;
  }
  const a = A[i];
  const b = A[i + 1];
  const { D, rest, l1, l2 } = between(a, b, lane, sh);
  const u = t - a.t;
  if (u < l1) {
    const k = smoother(u / l1);
    sample.y = a.y + (rest - a.y) * k;
    sample.travel = travel + Math.abs(rest - a.y) * k;
  } else if (u > D - l2) {
    const k = smoother((u - (D - l2)) / l2);
    sample.y = rest + (b.y - rest) * k;
    sample.travel = travel + Math.abs(rest - a.y) + Math.abs(b.y - rest) * k;
  } else {
    sample.y = rest;
    sample.travel = travel + Math.abs(rest - a.y);
  }
  return sample;
}

export function strandY(st: Strand, t: number, sh: Shape): number {
  return strandAt(st, t, sh).y;
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
  const end = Math.max(now, ...sorted.map((m) => m.year + 1));
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
    const s: Strand = { person: p, lane, start, fromParents, end, anchors, momentIds: mine.map((k) => k.moment.id) };
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
  return {
    strands: out,
    birthOrder,
    knots,
    laneCount: ordered.length,
    tMin: Math.floor(born / 5) * 5 - 3,
    tMax: end + 3,
    tNow: end,
  };
}

/**
 * Re-solve birth points for the current zoom: a child's thread starts
 * exactly between its parents' threads as they are drawn right now.
 */
export function relax(model: ThreadsModel, sh: Shape) {
  for (const s of model.birthOrder) {
    const ps = s.start.parents;
    if (!ps) continue;
    let y = 0;
    for (const q of ps) y += strandY(q, s.start.t, sh);
    s.start.y = y / ps.length;
  }
}
