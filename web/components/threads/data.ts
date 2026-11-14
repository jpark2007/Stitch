/**
 * Turns what the API sends (GET /families/{fid}/threads and, for family
 * links, GET /families/{fid}/tree) into the people and moments the threads
 * layout reads, and makes real-world data safe to draw: unknown birth
 * years, moments without a year or without anyone known in them, missing or
 * odd colours, duplicate ids, parent links that loop, and long names.
 *
 * Pure, no DOM, no runtime imports, erasable TypeScript only, so
 * `node --test` loads it directly (data.test.ts).
 */

import type { MomentSummary, Person, PersonSummary } from "@/lib/types";
import type { ThreadMoment, ThreadPerson } from "./model";

/** A moment that cannot be placed on the timeline, and why. */
export interface LooseMoment {
  id: string;
  title: string;
  /** "year": no usable year. "people": nobody in it is in the family's people. */
  reason: "year" | "people";
}

export interface ThreadsData {
  people: ThreadPerson[];
  moments: ThreadMoment[];
  /** Moments that are kept off the timeline, oldest added first. */
  loose: LooseMoment[];
}

/** Earliest year a moment or birth is believed. Anything before is a typo or a placeholder. */
const EARLIEST = 1700;

/** Fallback thread colours, warm and distinct, for people without a usable one. */
const FALLBACK_COLORS = ["#b9573d", "#5b82a6", "#7f9873", "#8b5a7c", "#d3a03d", "#2f8f8a", "#a0683f", "#6b6fae"];

/** Typical gap between a parent's birth and a child's, for guessing unknown birth years. */
const GENERATION = 27;

/** Names longer than this are shortened on the canvas (the full name stays everywhere else). */
const LABEL_MAX = 16;
/** Moment titles longer than this are shortened on the canvas. */
const TITLE_MAX = 42;

function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** `#rgb` or `#rrggbb` (any case) as `#rrggbb`, else null. */
export function normalizeColor(c: string | null | undefined): string | null {
  if (typeof c !== "string") return null;
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c.trim());
  if (!m) return null;
  const hex = m[1].length === 3 ? [...m[1]].map((x) => x + x).join("") : m[1];
  return `#${hex.toLowerCase()}`;
}

export function fallbackColor(id: string): string {
  return FALLBACK_COLORS[hashString(id) % FALLBACK_COLORS.length];
}

/** A year worth drawing: a whole number from EARLIEST to a year past `now`. */
export function usableYear(y: unknown, now: number): y is number {
  return typeof y === "number" && Number.isFinite(y) && y >= EARLIEST && y <= now + 1;
}

function clip(text: string, max: number): string {
  const chars = [...text];
  if (chars.length <= max) return text;
  return `${chars.slice(0, max - 1).join("").trimEnd()}…`;
}

/** A moment title short enough to sit by its knot. */
export function shortTitle(title: string): string {
  return clip(title.trim().replace(/\s+/g, " "), TITLE_MAX);
}

/**
 * Canvas labels for each person: the full name when it is short, else the
 * first name, else "First L.", so people who share a first name stay apart.
 */
export function threadLabels(people: { id: string; name: string }[]): Map<string, string> {
  const words = (n: string) => n.trim().split(/\s+/).filter(Boolean);
  const pick = (name: string, step: number): string => {
    const full = name.trim().replace(/\s+/g, " ") || "Someone";
    if ([...full].length <= LABEL_MAX || step === 3) return clip(full, LABEL_MAX);
    const w = words(full);
    if (step === 1 && w.length > 1) return clip(w[0], LABEL_MAX);
    if (step <= 2 && w.length > 1) return clip(`${w[0]} ${[...w[w.length - 1]][0]}.`, LABEL_MAX);
    return clip(full, LABEL_MAX);
  };
  const out = new Map<string, string>();
  let step = new Map(people.map((p) => [p.id, 1]));
  for (let round = 0; round < 3; round++) {
    const seen = new Map<string, string[]>();
    for (const p of people) {
      const label = pick(p.name, step.get(p.id)!);
      out.set(p.id, label);
      seen.set(label, [...(seen.get(label) ?? []), p.id]);
    }
    const next = new Map(step);
    let changed = false;
    for (const ids of seen.values()) {
      if (ids.length < 2) continue;
      for (const id of ids) {
        if (next.get(id)! < 3) {
          next.set(id, next.get(id)! + 1);
          changed = true;
        }
      }
    }
    if (!changed) break;
    step = next;
  }
  return out;
}

/** Drop parent links that point at unknown people, at oneself, or round in a loop. */
function acyclicParents(ids: string[], parentsOf: Map<string, string[]>): Map<string, string[]> {
  const known = new Set(ids);
  const out = new Map<string, string[]>();
  for (const id of ids) {
    out.set(id, [...new Set(parentsOf.get(id) ?? [])].filter((p) => p !== id && known.has(p)));
  }
  // Depth-first; an edge back to a person still on the stack closes a loop and is cut.
  const state = new Map<string, 1 | 2>();
  const visit = (id: string) => {
    state.set(id, 1);
    const kept: string[] = [];
    for (const p of out.get(id)!) {
      const s = state.get(p);
      if (s === 1) continue;
      if (s === undefined) visit(p);
      kept.push(p);
    }
    out.set(id, kept);
    state.set(id, 2);
  };
  for (const id of ids) if (!state.has(id)) visit(id);
  return out;
}

/**
 * Birth years for everyone. Known ones are kept; unknown ones are guessed
 * from parents, then children, then spouses, then the person's own first
 * moment, then the family's earliest; and never later than their first
 * moment, so a thread is always there for it.
 */
function birthYears(
  ids: string[],
  known: Map<string, number>,
  parents: Map<string, string[]>,
  spouses: Map<string, string[]>,
  firstMoment: Map<string, number>,
  now: number,
): Map<string, number> {
  const born = new Map(known);
  const children = new Map<string, string[]>();
  for (const id of ids) for (const p of parents.get(id) ?? []) children.set(p, [...(children.get(p) ?? []), id]);
  const avg = (ys: number[]) => Math.round(ys.reduce((a, b) => a + b, 0) / ys.length);
  // A few passes let guesses travel along the family (grandparent -> parent -> child).
  for (let pass = 0; pass < 4; pass++) {
    for (const id of ids) {
      if (born.has(id)) continue;
      const fromParents = (parents.get(id) ?? []).map((p) => born.get(p)).filter((y): y is number => y !== undefined);
      if (fromParents.length) {
        born.set(id, Math.max(...fromParents) + GENERATION);
        continue;
      }
      const fromKids = (children.get(id) ?? []).map((c) => born.get(c)).filter((y): y is number => y !== undefined);
      if (fromKids.length) {
        born.set(id, Math.min(...fromKids) - GENERATION);
        continue;
      }
      const fromSpouses = (spouses.get(id) ?? []).map((s) => born.get(s)).filter((y): y is number => y !== undefined);
      if (fromSpouses.length && pass > 0) born.set(id, avg(fromSpouses));
    }
  }
  const all = [...born.values(), ...firstMoment.values()];
  const floor = all.length ? Math.min(...all) : now - 30;
  for (const id of ids) {
    let y = born.get(id) ?? (firstMoment.has(id) ? firstMoment.get(id)! - 1 : floor);
    const first = firstMoment.get(id);
    if (!known.has(id) && first !== undefined) y = Math.min(y, first);
    born.set(id, Math.min(now, y));
  }
  return born;
}

/**
 * The threads screen's people and moments from the API's answers.
 * `tree` (optional) adds parents and spouses and the full character colour;
 * without it every thread still draws, just without family pull or branches.
 */
export function toThreadsData(
  threads: { people: PersonSummary[]; moments: MomentSummary[] },
  tree: { people: Person[] } | null,
  now: number,
): ThreadsData {
  // People: everyone either answer knows, threads order first, no duplicates.
  const full = new Map<string, Person>();
  for (const p of tree?.people ?? []) if (p && typeof p.id === "string" && !full.has(p.id)) full.set(p.id, p);
  const summaries = new Map<string, PersonSummary>();
  for (const p of threads.people ?? []) if (p && typeof p.id === "string" && !summaries.has(p.id)) summaries.set(p.id, p);
  const ids = [...new Set([...summaries.keys(), ...full.keys()])].filter((id) => id !== "");
  const idSet = new Set(ids);

  // Moments: split into drawable and loose.
  const moments: ThreadMoment[] = [];
  const loose: LooseMoment[] = [];
  const seenMoments = new Set<string>();
  for (const m of threads.moments ?? []) {
    if (!m || typeof m.id !== "string" || seenMoments.has(m.id)) continue;
    seenMoments.add(m.id);
    const title = typeof m.title === "string" && m.title.trim() ? m.title.trim() : "Untitled moment";
    const participantIds = [...new Set(m.participantIds ?? [])].filter((id) => idSet.has(id));
    if (!usableYear(m.year, now)) loose.push({ id: m.id, title, reason: "year" });
    else if (participantIds.length === 0) loose.push({ id: m.id, title, reason: "people" });
    else moments.push({ id: m.id, title, label: shortTitle(title), year: Math.floor(m.year), participantIds });
  }

  const firstMoment = new Map<string, number>();
  for (const m of moments) {
    for (const id of m.participantIds) firstMoment.set(id, Math.min(firstMoment.get(id) ?? Infinity, m.year));
  }

  const parents = acyclicParents(
    ids,
    new Map(ids.map((id) => [id, full.get(id)?.parentIds ?? []])),
  );
  const spouses = new Map(
    ids.map((id) => [id, [...new Set(full.get(id)?.spouseIds ?? [])].filter((s) => s !== id && idSet.has(s))]),
  );
  const knownBorn = new Map<string, number>();
  for (const id of ids) {
    const y = full.get(id)?.born ?? summaries.get(id)?.born;
    if (usableYear(y, now)) knownBorn.set(id, Math.floor(y));
  }
  const born = birthYears(ids, knownBorn, parents, spouses, firstMoment, now);

  const names = new Map(
    ids.map((id) => {
      const raw = full.get(id)?.name ?? summaries.get(id)?.name;
      return [id, typeof raw === "string" && raw.trim() ? raw.trim().replace(/\s+/g, " ") : "Someone"];
    }),
  );
  const labels = threadLabels(ids.map((id) => ({ id, name: names.get(id)! })));

  const diedIn = (id: string): number | null => {
    const y = full.get(id)?.died;
    return usableYear(y, now) && y >= born.get(id)! ? Math.floor(y) : null;
  };

  const people: ThreadPerson[] = ids.map((id) => ({
    id,
    name: names.get(id)!,
    label: labels.get(id),
    born: born.get(id)!,
    died: diedIn(id),
    parentIds: parents.get(id)!,
    spouseIds: spouses.get(id)!,
    color:
      normalizeColor(full.get(id)?.character?.color) ?? normalizeColor(summaries.get(id)?.color) ?? fallbackColor(id),
  }));

  return { people, moments, loose };
}
