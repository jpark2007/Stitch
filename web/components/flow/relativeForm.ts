// Pure logic behind "Add a relative": someone in the family who may never
// sign up (a late great-grandparent, a young child). The draft the form
// edits, its checks, the NewPerson it sends, the follow-up patches that make
// the new person a parent of the children picked, the `?added=` hand-off to
// the page that sent us here, and friendly words for the server's 400s.
//
// ARCHITECTURE.md section 4 item 5 over POST /families/{fid}/people and
// PATCH /families/{fid}/people/{id}. The server stays the source of truth
// (api/db/people.py InvalidRelation); these checks only catch what is cheap
// to see before a round trip.
//
// Erasable TypeScript only, no React. The runtime imports are extensionless
// for the Next build; __tests__/relativeForm.test.mjs adds a resolve hook so
// `node --test` finds them.
import type { NewPerson, PersonPatch } from "@/lib/types";
import { MAX_PARENTS, sortPeopleForPicking } from "./relationships";
import { validateName, validateYear, type FieldErrors } from "./validation";

/** What the form edits. Years stay strings until they are sent. */
export interface RelativeDraft {
  name: string;
  born: string;
  died: string;
  parentIds: string[];
  spouseIds: string[];
  /** People already in the tree who get the new person as a parent. */
  childIds: string[];
}

export type RelativeField = "name" | "born" | "died" | "parentIds" | "spouseIds" | "childIds";

/** Every field, in form order (for mergeServerErrors and focusing the first error). */
export const RELATIVE_FIELDS: readonly RelativeField[] = [
  "name",
  "born",
  "died",
  "parentIds",
  "spouseIds",
  "childIds",
];

/** Earliest year accepted: old enough for great-great-grandparents. */
export const RELATIVE_YEAR_MIN = 1700;

/** The bits of a tree person these helpers read (a Person fits). */
export interface TreePersonLite {
  id: string;
  name: string;
  born?: number | null;
  parentIds?: string[];
  spouseIds?: string[];
}

/** A fresh, empty draft. */
export function emptyRelative(): RelativeDraft {
  return { name: "", born: "", died: "", parentIds: [], spouseIds: [], childIds: [] };
}

/** Order-keeping dedupe that also drops blank ids. */
function uniqueIds(ids: readonly string[]): string[] {
  const out: string[] = [];
  for (const id of ids) {
    if (typeof id === "string" && id && !out.includes(id)) out.push(id);
  }
  return out;
}

function parentCount(p: TreePersonLite): number {
  return uniqueIds(p.parentIds ?? []).length;
}

/**
 * Who can be picked as the new person's child: everyone with fewer than two
 * parents who is not in `excludeIds` (the parents and spouses already picked),
 * oldest first, like the other pickers.
 */
export function childCandidates(
  people: TreePersonLite[],
  excludeIds: readonly string[],
): TreePersonLite[] {
  const excluded = new Set(excludeIds);
  return sortPeopleForPicking(
    people.filter((p) => p && !excluded.has(p.id) && parentCount(p) < MAX_PARENTS),
  );
}

/** Everyone above `ids` through parentIds (not `ids` themselves, unless a loop says so). */
function ancestorsOf(byId: Map<string, TreePersonLite>, ids: readonly string[]): Set<string> {
  const seen = new Set<string>();
  const queue = [...ids];
  while (queue.length) {
    const id = queue.shift() as string;
    for (const parent of byId.get(id)?.parentIds ?? []) {
      if (!seen.has(parent)) {
        seen.add(parent);
        queue.push(parent);
      }
    }
  }
  return seen;
}

/** Everyone below `ids` through parentIds (not `ids` themselves, unless a loop says so). */
function descendantsOf(people: TreePersonLite[], ids: readonly string[]): Set<string> {
  const children = new Map<string, string[]>();
  for (const p of people) {
    for (const parent of p.parentIds ?? []) {
      const list = children.get(parent) ?? [];
      list.push(p.id);
      children.set(parent, list);
    }
  }
  const seen = new Set<string>();
  const queue = [...ids];
  while (queue.length) {
    const id = queue.shift() as string;
    for (const child of children.get(id) ?? []) {
      if (!seen.has(child)) {
        seen.add(child);
        queue.push(child);
      }
    }
  }
  return seen;
}

function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function parseYear(v: string): number | null {
  const s = v.trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isInteger(n) ? n : null;
}

const MSG = {
  tooManyParents: "Pick up to two parents.", // MAX_PARENTS
  duplicate: "Each person can only be picked once.",
  unknown: "Someone you picked isn’t in this family’s tree any more. Unpick them and try again.",
  parentAndSpouse: "Someone can’t be both a parent and a spouse. Pick one for each person.",
  parentAndChild: "Someone can’t be both their parent and their child.",
  spouseAndChild: "Someone can’t be both their spouse and their child.",
  spouseInLine: "They can’t be married to someone in their own line, like a grandparent or a grandchild.",
  diedBeforeBorn: "The year they died can’t be before the year they were born.",
} as const;

/**
 * Checks the draft against the family as the page last saw it. Returns one
 * message per field that has a problem; an empty object means it can be sent.
 */
export function validateRelative(d: RelativeDraft, people: TreePersonLite[]): FieldErrors<RelativeField> {
  const errors: FieldErrors<RelativeField> = {};
  const byId = new Map(people.map((p) => [p.id, p]));
  const nameOf = (id: string) => byId.get(id)?.name?.trim() || "Someone";

  const name = validateName(d.name, "their name");
  if (name) errors.name = name;

  const yearOpts = { min: RELATIVE_YEAR_MIN };
  const born = validateYear(d.born, yearOpts);
  if (born) errors.born = born;
  const died = validateYear(d.died, yearOpts);
  if (died) errors.died = died;
  if (!born && !died) {
    const b = parseYear(d.born);
    const x = parseYear(d.died);
    if (b !== null && x !== null && x < b) errors.died = MSG.diedBeforeBorn;
  }

  const parents = d.parentIds;
  const spouses = d.spouseIds;
  const children = d.childIds;
  const has = (id: string) => byId.has(id);

  // Parents: how many, each once, all known.
  if (parents.length > MAX_PARENTS) errors.parentIds = MSG.tooManyParents;
  else if (new Set(parents).size !== parents.length) errors.parentIds = MSG.duplicate;
  else if (!parents.every(has)) errors.parentIds = MSG.unknown;

  // Spouses: each once, all known, not also a parent or a child, not in their own line.
  if (new Set(spouses).size !== spouses.length) errors.spouseIds = MSG.duplicate;
  else if (!spouses.every(has)) errors.spouseIds = MSG.unknown;
  else if (spouses.some((id) => parents.includes(id))) errors.spouseIds = MSG.parentAndSpouse;
  else if (spouses.some((id) => children.includes(id))) errors.spouseIds = MSG.spouseAndChild;
  else {
    // The new person's line: the parents and everyone above them, and the
    // children picked and everyone below them (they have no others yet).
    const line = new Set<string>([
      ...ancestorsOf(byId, parents),
      ...children,
      ...descendantsOf(people, children),
    ]);
    if (spouses.some((id) => line.has(id))) errors.spouseIds = MSG.spouseInLine;
  }

  // Children: each once, all known, not also a parent, still free to take a
  // parent, and not someone above the new person (that would be a loop).
  if (new Set(children).size !== children.length) errors.childIds = MSG.duplicate;
  else if (!children.every(has)) errors.childIds = MSG.unknown;
  else if (children.some((id) => parents.includes(id))) errors.childIds = MSG.parentAndChild;
  else {
    const free = new Set(childCandidates(people, []).map((p) => p.id));
    const full = children.filter((id) => !free.has(id));
    if (full.length) {
      const names = joinNames(full.map(nameOf));
      errors.childIds =
        full.length === 1
          ? `${names} already has two parents, so they can’t take another.`
          : `${names} already have two parents each, so they can’t take another.`;
    } else {
      const above = ancestorsOf(byId, parents);
      const loop = children.filter((id) => above.has(id));
      if (loop.length) {
        const names = joinNames(loop.map(nameOf));
        errors.childIds =
          loop.length === 1
            ? `${names} is already one of their ancestors, so ${names} can’t also be their child.`
            : `${names} are already their ancestors, so they can’t also be their children.`;
      }
    }
  }

  return errors;
}

/** The POST /families/{fid}/people body. Children are linked afterwards (childPatches). */
export function toNewPerson(d: RelativeDraft): NewPerson {
  return {
    name: d.name.trim(),
    born: parseYear(d.born),
    died: parseYear(d.died),
    parentIds: uniqueIds(d.parentIds),
    spouseIds: uniqueIds(d.spouseIds),
  };
}

/**
 * One PATCH per picked child, adding the new person to that child's parents
 * (keeping the ones they already have, each once).
 */
export function childPatches(
  people: TreePersonLite[],
  newId: string,
  childIds: readonly string[],
): Array<{ personId: string; patch: PersonPatch }> {
  const byId = new Map(people.map((p) => [p.id, p]));
  return uniqueIds(childIds).map((personId) => ({
    personId,
    patch: { parentIds: uniqueIds([...(byId.get(personId)?.parentIds ?? []), newId]) },
  }));
}

/**
 * `next` with `added=<personId>` in its query, replacing any `added` already
 * there, so the page we return to can preselect the new person. A #hash
 * stays at the end.
 */
export function withAddedParam(next: string, personId: string): string {
  const hashAt = next.indexOf("#");
  const hash = hashAt === -1 ? "" : next.slice(hashAt);
  const beforeHash = hashAt === -1 ? next : next.slice(0, hashAt);
  const queryAt = beforeHash.indexOf("?");
  const path = queryAt === -1 ? beforeHash : beforeHash.slice(0, queryAt);
  const query = queryAt === -1 ? "" : beforeHash.slice(queryAt + 1);
  const kept = query
    .split("&")
    .filter((part) => part !== "" && part.split("=")[0] !== "added");
  kept.push(`added=${encodeURIComponent(personId)}`);
  return `${path}?${kept.join("&")}${hash}`;
}

/**
 * The server's 400 details for people (api/db/people.py InvalidRelation and
 * the blank-name check in api/routes/families.py), in friendlier words and on
 * the field they belong to. Anything else comes back as is, on no field.
 */
export function relativeServerError(detail: string): { field: RelativeField | null; message: string } {
  const d = (detail ?? "").trim();
  const key = d.toLowerCase();
  if (key === "a person has at most 2 parents") return { field: "parentIds", message: MSG.tooManyParents };
  if (key === "duplicate parent id") return { field: "parentIds", message: MSG.duplicate };
  if (key === "a person cannot be their own parent or spouse") {
    return { field: "parentIds", message: "Someone can’t be their own parent or spouse." };
  }
  if (key === "a person cannot be both a parent and a spouse") {
    return { field: "spouseIds", message: MSG.parentAndSpouse };
  }
  if (key.startsWith("unknown person id(s) in this family")) {
    return {
      field: null,
      message: "Someone you picked isn’t in this family’s tree any more. Reload the page and pick again.",
    };
  }
  if (key === "a person cannot be married to their own ancestor or descendant") {
    return { field: "spouseIds", message: MSG.spouseInLine };
  }
  if (key === "a person cannot be their own ancestor") {
    return {
      field: "childIds",
      message: "That would make someone their own ancestor. Check the children you picked.",
    };
  }
  if (key === "a person needs a name") return { field: "name", message: "Enter their name." };
  return { field: null, message: d };
}
