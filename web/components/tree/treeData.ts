/**
 * Turns API people (or the local demo family) into what the tree draws, and
 * keeps odd data from breaking the drawing: repeated ids, blank names, colours
 * that are not hex, unknown builds. Siblings are ordered oldest first.
 *
 * Type-only imports, so `node --test` loads this file directly.
 * Tests: components/tree/treeData.test.ts.
 */

import type { CharacterSheet, Person } from "@/lib/types";

/** The bits of a contract `Person` the tree reads, so API people plug straight in. */
export type TreePerson = Pick<Person, "id" | "name" | "parentIds" | "spouseIds"> & {
  character: Pick<CharacterSheet, "color" | "item" | "build">;
};

/** A person as either source gives them: an API `Person` or a demo person. */
export interface SourcePerson {
  id: string;
  name?: string | null;
  born?: number | null;
  parentIds?: readonly string[] | null;
  spouseIds?: readonly string[] | null;
  character?: {
    color?: string | null;
    item?: string | null;
    build?: string | null;
  } | null;
}

/** Warm thread colours for anyone whose sheet has no usable one. */
const FALLBACK_COLORS = ["#b9573d", "#5b82a6", "#7f9873", "#8b5a7c", "#d3a03d", "#2f8f8a", "#c0503a"];

const BUILDS = new Set(["small", "short", "average", "tall"]);
const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

/** Stable small hash, so a person keeps the same fallback colour. */
function pick(id: string, n: number): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h % n;
}

/** `#rgb` / `#rrggbb` as `#rrggbb`, else null. */
export function cleanColor(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const c = raw.trim();
  if (!HEX.test(c)) return null;
  return c.length === 4 ? `#${[...c.slice(1)].map((ch) => ch + ch).join("")}`.toLowerCase() : c.toLowerCase();
}

/**
 * The people to draw, cleaned and in a pleasant order: oldest first (people
 * without a birth year keep their place after those with one), so brothers
 * and sisters read left to right by age.
 */
export function toTreePeople(source: readonly SourcePerson[]): TreePerson[] {
  const seen = new Set<string>();
  const kept: { p: SourcePerson; i: number }[] = [];
  source.forEach((p, i) => {
    if (!p || typeof p.id !== "string" || !p.id || seen.has(p.id)) return;
    seen.add(p.id);
    kept.push({ p, i });
  });
  const born = (p: SourcePerson) => (typeof p.born === "number" && Number.isFinite(p.born) ? p.born : Infinity);
  kept.sort((a, b) => born(a.p) - born(b.p) || a.i - b.i);

  const ids = (list: readonly string[] | null | undefined) =>
    Array.isArray(list) ? list.filter((id): id is string => typeof id === "string" && seen.has(id)) : [];

  return kept.map(({ p }) => {
    const ch = p.character ?? {};
    const build = typeof ch.build === "string" && BUILDS.has(ch.build) ? (ch.build as TreePerson["character"]["build"]) : undefined;
    const item = typeof ch.item === "string" && ch.item.trim() ? ch.item.trim() : null;
    const name = typeof p.name === "string" && p.name.trim() ? p.name.trim().replace(/\s+/g, " ") : "Someone";
    return {
      id: p.id,
      name,
      parentIds: ids(p.parentIds),
      spouseIds: ids(p.spouseIds),
      character: {
        color: cleanColor(ch.color) ?? FALLBACK_COLORS[pick(p.id, FALLBACK_COLORS.length)],
        item,
        build,
      },
    };
  });
}
