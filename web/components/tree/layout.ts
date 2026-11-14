/**
 * Automatic family-tree layout from `parentIds` / `spouseIds` alone.
 *
 * 1. Generations: a person sits one row below their lowest parent; spouses
 *    share a row (a married-in spouse with no parents joins their partner's).
 * 2. Units: spouses in the same row form one unit and stay side by side.
 * 3. Order: barycentre sweeps (down by parents, up by children) so sibling
 *    groups sit under their parents and lines cross as little as possible.
 * 4. Position: a few relaxation passes pull each unit toward its parents and
 *    children, while keeping minimum gaps between neighbours.
 *
 * Everything is in abstract "layout units"; the renderer scales it.
 */

export interface LayoutPerson {
  id: string;
  /** Missing means none (matches the optional arrays on the contract Person). */
  parentIds?: readonly string[];
  spouseIds?: readonly string[];
}

export interface LayoutOptions {
  /** Centre-to-centre distance between spouses. */
  spouseStep: number;
  /** Footprint of one person (their node width). */
  nodeWidth: number;
  /** Edge gap between siblings / units of the same family. */
  siblingGap: number;
  /** Edge gap between units from different families. */
  familyGap: number;
  /** Row-to-row distance. */
  rowHeight: number;
  /** Padding around the whole tree. */
  margin: number;
}

export interface PlacedPerson {
  id: string;
  gen: number;
  x: number;
  y: number;
}

/** A set of children and the parents they hang from. */
export interface PlacedFamily {
  key: string;
  parentIds: string[];
  childIds: string[];
  /** Where the children's lines start: a couple's midpoint, or the lone parent. */
  anchorX: number;
  /** True when the parents are a couple placed side by side. */
  fromCouple: boolean;
}

export interface PlacedCouple {
  a: string;
  b: string;
  /** Midpoint between the two partners. */
  x: number;
  y: number;
}

export interface TreeLayout {
  width: number;
  height: number;
  rows: number;
  people: PlacedPerson[];
  byId: Map<string, PlacedPerson>;
  couples: PlacedCouple[];
  families: PlacedFamily[];
}

export const DEFAULT_LAYOUT: LayoutOptions = {
  spouseStep: 118,
  nodeWidth: 112,
  siblingGap: 34,
  familyGap: 64,
  rowHeight: 236,
  margin: 28,
};

interface Unit {
  members: string[];
  /** Centre x. */
  x: number;
}

export function layoutFamily(
  given: readonly LayoutPerson[],
  opts: LayoutOptions = DEFAULT_LAYOUT,
): TreeLayout {
  // One entry per id (the first wins), so duplicates never place a person twice.
  const people = new Map<string, LayoutPerson>();
  for (const p of given) if (!people.has(p.id)) people.set(p.id, p);
  const input = [...people.values()];
  if (!input.length) {
    return { width: 0, height: 0, rows: 0, people: [], byId: new Map(), couples: [], families: [] };
  }
  const dataIndex = new Map(input.map((p, i) => [p.id, i]));

  // Links to people outside the tree, or to oneself, are ignored. Spouse links
  // count from either side, so a one-sided link still pairs the two.
  const parentMap = new Map(
    input.map((p) => [p.id, [...new Set(p.parentIds ?? [])].filter((q) => q !== p.id && people.has(q))]),
  );
  const parentsOf = (id: string): string[] => parentMap.get(id) ?? [];
  const spouseMap = new Map<string, string[]>(input.map((p) => [p.id, []]));
  for (const p of input) {
    for (const s of p.spouseIds ?? []) {
      if (s === p.id || !people.has(s)) continue;
      if (!spouseMap.get(p.id)!.includes(s)) spouseMap.get(p.id)!.push(s);
      if (!spouseMap.get(s)!.includes(p.id)) spouseMap.get(s)!.push(p.id);
    }
  }
  const spousesOf = (id: string) => spouseMap.get(id) ?? [];

  const childrenOf = new Map<string, string[]>();
  for (const p of input) {
    for (const parent of parentsOf(p.id)) {
      const list = childrenOf.get(parent) ?? [];
      list.push(p.id);
      childrenOf.set(parent, list);
    }
  }

  // --- 1. Generations -------------------------------------------------------
  const gen = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (id: string): number => {
    const known = gen.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0; // defensive: ignore cycles
    visiting.add(id);
    const ps = parentsOf(id);
    const g = ps.length ? 1 + Math.max(...ps.map(depth)) : 0;
    visiting.delete(id);
    gen.set(id, g);
    return g;
  };
  input.forEach((p) => depth(p.id));

  // Spouses share a row; a child must stay below its parents. Iterate to a fixpoint.
  for (let changed = true, guard = 0; changed && guard < 50; guard++) {
    changed = false;
    for (const p of input) {
      let g = gen.get(p.id)!;
      for (const s of spousesOf(p.id)) g = Math.max(g, gen.get(s)!);
      for (const parent of parentsOf(p.id)) g = Math.max(g, gen.get(parent)! + 1);
      if (g !== gen.get(p.id)) {
        gen.set(p.id, g);
        changed = true;
      }
    }
  }
  // Close any empty rows (only bad links can leave them), keeping the order.
  const used = [...new Set(gen.values())].sort((a, b) => a - b);
  const dense = new Map(used.map((g, i) => [g, i]));
  for (const [id, g] of gen) gen.set(id, dense.get(g)!);
  const rows = used.length;

  // --- 2. Units (spouse groups within one row) -------------------------------
  const unitOf = new Map<string, Unit>();
  const rowUnits: Unit[][] = Array.from({ length: rows }, () => []);
  for (const p of input) {
    if (unitOf.has(p.id)) continue;
    const g = gen.get(p.id)!;
    const members: string[] = [];
    const stack = [p.id];
    while (stack.length) {
      const id = stack.pop()!;
      if (members.includes(id)) continue;
      members.push(id);
      for (const s of spousesOf(id)) if (gen.get(s) === g && !members.includes(s)) stack.push(s);
    }
    const unit: Unit = { members: chainOrder(members, spousesOf, dataIndex), x: 0 };
    members.forEach((m) => unitOf.set(m, unit));
    rowUnits[g].push(unit);
  }

  const unitWidth = (u: Unit) => (u.members.length - 1) * opts.spouseStep + opts.nodeWidth;
  const memberX = (id: string) => {
    const u = unitOf.get(id)!;
    const i = u.members.indexOf(id);
    return u.x + (i - (u.members.length - 1) / 2) * opts.spouseStep;
  };

  const keyOf = new Map(input.map((p) => [p.id, [...parentsOf(p.id)].sort().join("+")]));
  const familyKey = (id: string) => keyOf.get(id) ?? "";
  /** Everyone sharing a set of parents, by family key. */
  const siblingsByKey = new Map<string, string[]>();
  for (const p of input) {
    const k = familyKey(p.id);
    if (k) siblingsByKey.set(k, [...(siblingsByKey.get(k) ?? []), p.id]);
  }
  const anchorOf = (id: string) => {
    const ps = parentsOf(id);
    return ps.reduce((s, p) => s + memberX(p), 0) / ps.length;
  };

  /** Two adjacent units belong to the same family if any members are siblings. */
  const related = (a: Unit, b: Unit) =>
    a.members.some((m) => {
      const k = familyKey(m);
      return k !== "" && b.members.some((n) => familyKey(n) === k);
    });

  const gapBetween = (a: Unit, b: Unit) =>
    (unitWidth(a) + unitWidth(b)) / 2 + (related(a, b) ? opts.siblingGap : opts.familyGap);

  /** Lay a row out left to right at minimum spacing, centred on 0. */
  const pack = (row: Unit[]) => {
    let x = 0;
    row.forEach((u, i) => {
      if (i > 0) x += gapBetween(row[i - 1], u);
      u.x = x;
    });
    const mid = x / 2;
    row.forEach((u) => (u.x -= mid));
  };

  /** Stable sort a row by a key; units without one keep their current place. */
  const reorder = (row: Unit[], key: (u: Unit) => number | undefined) => {
    const keyed = row.map((u, i) => ({ u, i, k: key(u) ?? u.x }));
    keyed.sort((a, b) => a.k - b.k || a.i - b.i);
    row.splice(0, row.length, ...keyed.map((e) => e.u));
  };

  const mean = (xs: number[]) => (xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : undefined);
  const parentKey = (u: Unit) =>
    mean(u.members.filter((m) => parentsOf(m).length).map(anchorOf));
  const childKey = (u: Unit) =>
    mean(u.members.flatMap((m) => childrenOf.get(m) ?? []).map(memberX));

  /**
   * Inside a couple, put each partner on the side their own parents are on.
   * A unit is a chain of marriages (someone married twice sits between both
   * partners), so it only ever flips end to end, which keeps every partner
   * next to the one they married.
   */
  const orderMembers = (u: Unit) => {
    if (u.members.length < 2) return;
    const cost = (order: string[]) =>
      order.reduce((sum, m, i) => {
        if (!parentsOf(m).length) return sum;
        const x = u.x + (i - (order.length - 1) / 2) * opts.spouseStep;
        return sum + Math.abs(x - anchorOf(m));
      }, 0);
    const flipped = [...u.members].reverse();
    if (cost(flipped) < cost(u.members) - 0.001) u.members.splice(0, u.members.length, ...flipped);
  };

  // --- 3. Ordering sweeps ----------------------------------------------------
  rowUnits.forEach(pack);
  for (let sweep = 0; sweep < 3; sweep++) {
    for (let g = 1; g < rows; g++) {
      reorder(rowUnits[g], parentKey);
      rowUnits[g].forEach(orderMembers);
      pack(rowUnits[g]);
    }
    for (let g = rows - 2; g >= 0; g--) {
      reorder(rowUnits[g], childKey);
      pack(rowUnits[g]);
    }
  }
  for (let g = 1; g < rows; g++) rowUnits[g].forEach(orderMembers);

  // --- 4. Relaxation -----------------------------------------------------------
  const familyMeanX = (key: string) => mean((siblingsByKey.get(key) ?? []).map(memberX))!;

  const separate = (row: Unit[]) => {
    for (let pass = 0; pass < 24; pass++) {
      let moved = false;
      for (let i = 1; i < row.length; i++) {
        const need = gapBetween(row[i - 1], row[i]);
        const overlap = need - (row[i].x - row[i - 1].x);
        if (overlap > 0.01) {
          row[i - 1].x -= overlap / 2;
          row[i].x += overlap / 2;
          moved = true;
        }
      }
      if (!moved) break;
    }
    // Guarantee the constraint with one final forward pass.
    for (let i = 1; i < row.length; i++) {
      const need = gapBetween(row[i - 1], row[i]);
      if (row[i].x - row[i - 1].x < need) row[i].x = row[i - 1].x + need;
    }
  };

  for (let iter = 0; iter < 80; iter++) {
    for (let g = 0; g < rows; g++) {
      for (const u of rowUnits[g]) {
        const shifts: number[] = [];
        // Sit my sibling group under my parents.
        for (const m of u.members) {
          if (parentsOf(m).length) shifts.push(anchorOf(m) - familyMeanX(familyKey(m)));
        }
        // Sit over my children.
        const kids = u.members.flatMap((m) => childrenOf.get(m) ?? []);
        if (kids.length) {
          const parentGroups = new Set(kids.map(familyKey));
          for (const key of parentGroups) {
            const ps = key.split("+");
            const anchor = mean(ps.map(memberX))!;
            shifts.push(familyMeanX(key) - anchor);
          }
        }
        const s = mean(shifts);
        if (s !== undefined) u.x += s * 0.35;
      }
      separate(rowUnits[g]);
    }
  }

  // Settle each couple's sides now that everyone is in place. Only plain
  // couples: flipping one keeps its midpoint, so their children's lines stay
  // put (a longer chain would move them).
  for (let g = 1; g < rows; g++) {
    rowUnits[g].forEach((u) => {
      if (u.members.length === 2) orderMembers(u);
    });
  }

  // --- Output ------------------------------------------------------------------
  const placed: PlacedPerson[] = input.map((p) => ({
    id: p.id,
    gen: gen.get(p.id)!,
    x: memberX(p.id),
    y: gen.get(p.id)! * opts.rowHeight,
  }));
  const minX = Math.min(...placed.map((p) => p.x)) - opts.nodeWidth / 2 - opts.margin;
  const maxX = Math.max(...placed.map((p) => p.x)) + opts.nodeWidth / 2 + opts.margin;
  placed.forEach((p) => {
    p.x -= minX;
    p.y += opts.margin;
  });
  const byId = new Map(placed.map((p) => [p.id, p]));

  const couples: PlacedCouple[] = [];
  for (const row of rowUnits) {
    for (const u of row) {
      for (let i = 1; i < u.members.length; i++) {
        const a = byId.get(u.members[i - 1])!;
        const b = byId.get(u.members[i])!;
        // Neighbours in a unit who are not married (two partners of one
        // person, in a tangle the chain could not lay flat) hold no thread.
        if (!spousesOf(a.id).includes(b.id)) continue;
        couples.push({ a: a.id, b: b.id, x: (a.x + b.x) / 2, y: a.y });
      }
    }
  }

  const families = new Map<string, PlacedFamily>();
  for (const p of input) {
    const ps = parentsOf(p.id);
    if (!ps.length) continue;
    const key = familyKey(p.id);
    let fam = families.get(key);
    if (!fam) {
      const xs = ps.map((id) => byId.get(id)!.x);
      const fromCouple =
        ps.length === 2 &&
        couples.some((c) => (c.a === ps[0] && c.b === ps[1]) || (c.a === ps[1] && c.b === ps[0]));
      fam = {
        key,
        parentIds: ps,
        childIds: [],
        anchorX: xs.reduce((s, v) => s + v, 0) / xs.length,
        fromCouple,
      };
      families.set(key, fam);
    }
    fam.childIds.push(p.id);
  }
  for (const fam of families.values()) fam.childIds.sort((a, b) => byId.get(a)!.x - byId.get(b)!.x);

  return {
    width: maxX - minX,
    height: (rows - 1) * opts.rowHeight + opts.margin * 2,
    rows,
    people: placed,
    byId,
    couples,
    families: [...families.values()],
  };
}

/**
 * Order a spouse group so that partners sit side by side. Most groups are a
 * couple; someone married twice makes a chain (first partner, them, second
 * partner), walked from the end that comes first in the data. Anything
 * tangier puts the most-married person in the middle with their partners
 * either side.
 */
function chainOrder(
  members: string[],
  spousesOf: (id: string) => readonly string[],
  dataIndex: ReadonlyMap<string, number>,
): string[] {
  const byData = [...members].sort((a, b) => dataIndex.get(a)! - dataIndex.get(b)!);
  if (byData.length < 3) return byData;
  const inUnit = new Set(byData);
  const links = (id: string) => spousesOf(id).filter((s) => inUnit.has(s));
  const isPath = byData.every((m) => links(m).length <= 2) && byData.some((m) => links(m).length === 1);
  if (isPath) {
    const order: string[] = [];
    let at: string | undefined = byData.find((m) => links(m).length === 1);
    while (at !== undefined && !order.includes(at)) {
      order.push(at);
      at = links(at)
        .filter((s) => !order.includes(s))
        .sort((a, b) => dataIndex.get(a)! - dataIndex.get(b)!)[0];
    }
    if (order.length === byData.length) return order;
  }
  const hub = byData.reduce((best, m) => (links(m).length > links(best).length ? m : best));
  const left: string[] = [];
  const right: string[] = [];
  links(hub).forEach((s, i) => (i % 2 === 0 ? left : right).push(s));
  const rest = byData.filter((m) => m !== hub && !left.includes(m) && !right.includes(m));
  return [...rest, ...left.reverse(), hub, ...right];
}
