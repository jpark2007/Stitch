// Pure logic behind the relationship picker ("child of Rosa and Luis,
// married to Ana"). No runtime imports and only erasable TypeScript, so
// `node --test` can load this file directly.

/** Who someone is related to, by person id. Mirrors NewPerson.parentIds / spouseIds. */
export interface Relationships {
  parentIds: string[];
  spouseIds: string[];
}

/** Nobody has more than two parents in the family tree. */
export const MAX_PARENTS = 2;

function hasDuplicates(ids: string[]): boolean {
  return new Set(ids).size !== ids.length;
}

/**
 * Checks a set of relationships before it is sent. Returns one plain-language
 * problem, or null when everything is fine.
 */
export function validateRelationships(
  r: Relationships,
  opts?: { selfId?: string },
): string | null {
  const selfId = opts?.selfId;
  if (selfId && (r.parentIds.includes(selfId) || r.spouseIds.includes(selfId))) {
    return "You can't pick yourself as your own parent or spouse.";
  }
  if (hasDuplicates(r.parentIds) || hasDuplicates(r.spouseIds)) {
    return "Each person can only be picked once.";
  }
  if (r.parentIds.length > MAX_PARENTS) {
    return `Pick up to ${MAX_PARENTS} parents.`;
  }
  if (r.parentIds.some((id) => r.spouseIds.includes(id))) {
    return "Someone can't be both a parent and a spouse. Pick one for each person.";
  }
  return null;
}

/**
 * Turns one person on or off in one role. Adding someone to a role takes them
 * out of the other one. Adding a third parent is ignored: the same object
 * comes back, so callers can tell nothing changed (`next === r`).
 */
export function toggleRelation(
  r: Relationships,
  kind: "parent" | "spouse",
  personId: string,
): Relationships {
  const own = kind === "parent" ? r.parentIds : r.spouseIds;
  const other = kind === "parent" ? r.spouseIds : r.parentIds;

  if (own.includes(personId)) {
    const nextOwn = own.filter((id) => id !== personId);
    return kind === "parent"
      ? { parentIds: nextOwn, spouseIds: [...other] }
      : { parentIds: [...other], spouseIds: nextOwn };
  }

  if (kind === "parent" && own.length >= MAX_PARENTS) return r;

  const nextOwn = [...own, personId];
  const nextOther = other.filter((id) => id !== personId);
  return kind === "parent"
    ? { parentIds: nextOwn, spouseIds: nextOther }
    : { parentIds: nextOther, spouseIds: nextOwn };
}

function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * Plain-language summary: "Child of Rosa and Luis, married to Ana",
 * "Married to Ana", or "No relatives picked yet".
 */
export function describeRelationships(
  r: Relationships,
  names: Record<string, string>,
): string {
  const nameOf = (id: string) => names[id]?.trim() || "someone";
  const parts: string[] = [];
  if (r.parentIds.length > 0) parts.push(`child of ${joinNames(r.parentIds.map(nameOf))}`);
  if (r.spouseIds.length > 0) parts.push(`married to ${joinNames(r.spouseIds.map(nameOf))}`);
  if (parts.length === 0) return "No relatives picked yet";
  const text = parts.join(", ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Oldest first by birth year, people with no known year last, then by name.
 * Returns a new array; the input is left alone.
 */
export function sortPeopleForPicking<T extends { name: string; born?: number | null }>(
  people: T[],
): T[] {
  return [...people].sort((a, b) => {
    const aKnown = Number.isFinite(a.born);
    const bKnown = Number.isFinite(b.born);
    if (aKnown && bKnown && a.born !== b.born) return (a.born as number) - (b.born as number);
    if (aKnown !== bKnown) return aKnown ? -1 : 1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  });
}
