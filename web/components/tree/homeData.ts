/**
 * What the home screen shows, decided from the API: who is signed in, which
 * of their families is current, and that family's tree. Every failure becomes
 * a view (signed out, offline, an error sentence), never a throw.
 *
 * Type-only imports, so `node --test` loads this file directly.
 * Tests: components/tree/homeData.test.ts.
 */

import type { StitchApi } from "@/lib/api";
import type { FamilySummary, Person } from "@/lib/types";

export type HomeView =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "no-family" }
  /** The API never answered (offline, wrong URL, server down). */
  | { kind: "offline" }
  | { kind: "error"; message: string }
  | {
      kind: "tree";
      familyId: string;
      familyName: string;
      /** As the API sent them; toTreePeople cleans them for drawing. */
      people: Person[];
      families: FamilySummary[];
      /** The signed-in person in this family, when they have claimed one. */
      myPersonId: string | null;
    };

export const FALLBACK_ERROR = "Something went wrong while opening your family's tree. Please try again.";

/** The bits of an ApiError this reads, so a plain object stands in for tests. */
function statusOf(e: unknown): number | null {
  if (typeof e !== "object" || e === null) return null;
  const s = (e as { status?: unknown }).status;
  return typeof s === "number" ? s : null;
}

function detailOf(e: unknown): string | null {
  if (typeof e !== "object" || e === null) return null;
  const d = (e as { detail?: unknown }).detail;
  return typeof d === "string" && d.trim() ? d.trim() : null;
}

/** A failed call as a view: 401 is signed out, status 0 is unreachable. */
export function viewForError(e: unknown): HomeView {
  const status = statusOf(e);
  if (status === 401) return { kind: "signed-out" };
  if (status === 0) return { kind: "offline" };
  if (status === null) return { kind: "error", message: FALLBACK_ERROR };
  return { kind: "error", message: detailOf(e) ?? FALLBACK_ERROR };
}

/** The preferred family when the user belongs to it, else their first, else null. */
export function chooseFamily(families: readonly FamilySummary[], preferred: string | null | undefined) {
  return (preferred ? families.find((f) => f.id === preferred) : undefined) ?? families[0] ?? null;
}

/**
 * Ask who is signed in, pick the current family (the preferred one if they
 * still belong to it) and fetch its tree.
 */
export async function loadHome(
  client: Pick<StitchApi, "me" | "getTree">,
  preferred: string | null | undefined,
): Promise<HomeView> {
  let families: FamilySummary[];
  try {
    const me = await client.me();
    families = Array.isArray(me?.families) ? me.families : [];
  } catch (e) {
    return viewForError(e);
  }
  const family = chooseFamily(families, preferred);
  if (!family) return { kind: "no-family" };
  try {
    const tree = await client.getTree(family.id);
    return {
      kind: "tree",
      familyId: family.id,
      familyName: (tree.familyName || family.name || "Your family").trim(),
      people: Array.isArray(tree.people) ? tree.people : [],
      families,
      myPersonId: family.personId ?? null,
    };
  } catch (e) {
    return viewForError(e);
  }
}

const enc = encodeURIComponent;

/** A person's book, in this family. */
export function personHref(personId: string, familyId?: string | null): string {
  return familyId ? `/people/${enc(personId)}?family=${enc(familyId)}` : `/people/${enc(personId)}`;
}

/** The threads screen, for this family. */
export function threadsHref(familyId?: string | null): string {
  return familyId ? `/threads?family=${enc(familyId)}` : "/threads";
}
