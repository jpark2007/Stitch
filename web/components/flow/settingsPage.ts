// Pure logic behind the family settings page: finding the family in the
// signed-in user's list, reading the `?welcome=1` flag that starting a family
// sets, and turning a failed load into what the page shows.
//
// Erasable TypeScript only. The one runtime import (../../lib/api) is
// extensionless for the Next build; __tests__/settingsPage.test.mjs adds a
// resolve hook so `node --test` finds it.
import type { FamilySummary } from "../../lib/types";
import { isApiError } from "../../lib/api";

/** The family `fid` among the user's families, or null when they are not in it. */
export function findFamily(families: FamilySummary[], fid: string): FamilySummary | null {
  if (!Array.isArray(families) || !fid) return null;
  return families.find((f) => f && f.id === fid) ?? null;
}

/**
 * True only when the query says `welcome=1`. Takes a search string (with or
 * without its "?"), URLSearchParams, or null.
 */
export function isWelcome(search: string | URLSearchParams | null): boolean {
  if (search == null) return false;
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  return params.get("welcome") === "1";
}

/** What the page shows when loading the family failed. */
export type SettingsLoad = { kind: "not-found" } | { kind: "error"; message: string };

/** Shown for a failure that is not an ApiError (a bug, say). */
export const SETTINGS_LOAD_FALLBACK =
  "Something went wrong while opening this family's settings. Please try again.";

/**
 * A 404 is "not found": the API answers 404, not 403, for a family the user
 * is not in. Any other ApiError shows its own message (status 0 is the
 * offline message); anything else shows a generic one.
 */
export function settingsLoadError(err: unknown): SettingsLoad {
  if (isApiError(err)) {
    if (err.status === 404) return { kind: "not-found" };
    const message = err.detail?.trim() || err.message?.trim() || SETTINGS_LOAD_FALLBACK;
    return { kind: "error", message };
  }
  return { kind: "error", message: SETTINGS_LOAD_FALLBACK };
}
