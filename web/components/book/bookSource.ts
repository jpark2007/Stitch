/**
 * Where a book comes from (a person's book, or one moment's), as one small
 * decision. It mirrors the tree and threads screens:
 *
 *   ?demo=1, or mock mode         -> "demo": the local demo family, no API call
 *   ?family=<fid>                 -> that family, whoever is current here; it
 *                                    becomes this browser's family when the
 *                                    user belongs to it (rememberFamily)
 *   signed in, no ?family=        -> the remembered family, else the first
 *                                    (pickFamily(me.families, recallFamily()))
 *   signed out (401)              -> "signed-out"
 *   signed in with no family      -> "no-family"
 *   API out of reach (status 0)   -> "unreachable": the page shows the demo,
 *                                    with a quiet note and a "try again"
 *   the person or moment is 404   -> "not-found" (also a ?family= the user is
 *                                    not in: the API answers 404 for it)
 *   anything else                 -> "error", with a sentence to show
 *
 *   const r = await resolveBook({ demo, family, mock }, { api, session, load: (fid) => api.getBook(fid, id) });
 *
 * Type-only imports, so node runs it directly (bookSource.test.ts). The
 * session helpers (components/flow/session.ts) and the API are passed in.
 */

import type { StitchApi } from "@/lib/api";
import type { FamilySummary, MeResponse } from "@/lib/types";

export interface BookScope {
  /** ?demo=1: the visitor chose the demo family. */
  demo: boolean;
  /** ?family=<fid>, trimmed; null when absent or empty. */
  family: string | null;
  /** isMockMode(): pages talk to the in-memory mock, which has no books. */
  mock: boolean;
}

/** The bits of components/flow/session.ts this reads and writes. */
export interface BookSession {
  pickFamily(families: FamilySummary[], preferred: string | null | undefined): FamilySummary | null;
  recallFamily(): string | null;
  rememberFamily(fid: string): void;
}

export type BookResolution<T> =
  | { kind: "demo"; why: "chosen" | "mock" }
  /** Stitch never answered (offline, server down): show the demo, say so, offer a retry. */
  | { kind: "unreachable" }
  | { kind: "signed-out" }
  | { kind: "no-family" }
  | { kind: "not-found" }
  | { kind: "error"; detail: string }
  | {
      kind: "family";
      familyId: string;
      /** The family as the user knows it (its name), when /me says they belong to it. */
      family: FamilySummary | null;
      data: T;
    };

export const BOOK_ERROR = "Something went wrong while opening this book. Please try again.";

/** An ApiError's status, duck-typed so plain objects stand in for tests. */
export function statusOf(e: unknown): number | null {
  if (typeof e !== "object" || e === null) return null;
  const s = (e as { status?: unknown }).status;
  return typeof s === "number" ? s : null;
}

function detailOf(e: unknown): string | null {
  if (typeof e !== "object" || e === null) return null;
  const d = (e as { detail?: unknown }).detail;
  return typeof d === "string" && d.trim() ? d.trim() : null;
}

const isAbort = (e: unknown) => typeof e === "object" && e !== null && (e as { name?: unknown }).name === "AbortError";

/**
 * A failed call as a resolution: 401 signed out, 0 unreachable, 404 not
 * found. Other 4xx show the API's sentence; 5xx and unknown failures a
 * friendly one (as the threads screen does).
 */
export function resolutionForError(e: unknown): Exclude<BookResolution<never>, { kind: "family" | "demo" }> {
  const status = statusOf(e);
  if (status === 401) return { kind: "signed-out" };
  if (status === 0) return { kind: "unreachable" };
  if (status === 404) return { kind: "not-found" };
  if (status !== null && status >= 400 && status < 500) return { kind: "error", detail: detailOf(e) ?? BOOK_ERROR };
  return { kind: "error", detail: BOOK_ERROR };
}

export interface ResolveDeps<T> {
  api: Pick<StitchApi, "me">;
  session: BookSession;
  /** Fetch the book from family `fid` (getBook, getMemory). Rejects with an ApiError. */
  load: (fid: string) => Promise<T>;
}

/**
 * Decide where the book comes from, and load it from the API when it is a
 * family's. An abort (AbortError from `load`) is rethrown: the caller has
 * moved on and nothing should settle.
 */
export async function resolveBook<T>(scope: BookScope, { api, session, load }: ResolveDeps<T>): Promise<BookResolution<T>> {
  if (scope.mock) return { kind: "demo", why: "mock" };
  if (scope.demo) return { kind: "demo", why: "chosen" };

  if (scope.family) {
    const fid = scope.family;
    // The link names the family: load from it straight away, and ask /me
    // alongside only to know whether it is one of the user's (to remember).
    const [me, book] = await Promise.allSettled([api.me(), load(fid)]);
    if (book.status === "rejected") {
      if (isAbort(book.reason)) throw book.reason;
      return resolutionForError(book.reason);
    }
    const family = me.status === "fulfilled" ? (familiesOf(me.value).find((f) => f.id === fid) ?? null) : null;
    if (family) session.rememberFamily(fid);
    return { kind: "family", familyId: fid, family, data: book.value };
  }

  let families: FamilySummary[];
  try {
    families = familiesOf(await api.me());
  } catch (e) {
    if (isAbort(e)) throw e;
    return resolutionForError(e);
  }
  const family = session.pickFamily(families, session.recallFamily());
  if (!family) return { kind: "no-family" };
  try {
    return { kind: "family", familyId: family.id, family, data: await load(family.id) };
  } catch (e) {
    if (isAbort(e)) throw e;
    return resolutionForError(e);
  }
}

function familiesOf(me: MeResponse | null | undefined): FamilySummary[] {
  return Array.isArray(me?.families) ? me.families : [];
}
