// Pure logic behind the family settings page: finding the family in the
// signed-in user's list, reading the `?welcome=1` flag that starting a family
// sets, naming who is signed in, and turning a failed load into what the page
// shows.
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

/**
 * The page for adding a relative to family `fid` (someone who may never sign
 * up). Any member may add people, so every member gets this link. Spelled
 * out here rather than in session.ts's hrefs, which another page owns.
 */
export function addRelativeHref(fid: string): string {
  return `/families/${encodeURIComponent(fid)}/people/new`;
}

/** "David", "David and Rosa", "David, Rosa and Luis"; "" for none. */
export function listOfNames(names: string[]): string {
  const list = (names ?? []).map((n) => (typeof n === "string" ? n.trim() : "")).filter(Boolean);
  if (list.length <= 1) return list[0] ?? "";
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

/** Enough of a MemberRow (./members) to name the admins. */
interface AdminCandidate {
  role: "admin" | "member";
  name: string;
  personId: string | null;
  isYou: boolean;
}

/**
 * For a member, who can share the invite link: "Ask David to send them the
 * invite link." Names the other admins we know by name (an admin with no
 * person in the tree has no name to give), else asks "an admin".
 */
export function inviteAskLine(rows: AdminCandidate[]): string {
  const admins = (rows ?? []).filter((r) => r && r.role === "admin" && !r.isYou && r.personId).map((r) => r.name);
  const who = listOfNames(admins) || "an admin";
  return `Ask ${who} to send them the invite link.`;
}

/**
 * Who is signed in, as the settings and join footers show it: "Maya
 * (maya3@example.com)" when the account has a name, else just the email.
 */
export function signedInLabel(user: { name?: string | null; email: string }): string {
  const name = typeof user?.name === "string" ? user.name.trim() : "";
  const email = typeof user?.email === "string" ? user.email.trim() : "";
  if (!name) return email;
  return email ? `${name} (${email})` : name;
}

/**
 * After a failed change on the page, what to fetch again so nothing stale
 * stays on screen. A 403, 404 or 409 means the page was out of date (someone
 * changed roles, left, or turned the link off elsewhere): fetch the family.
 * A 403 also means the viewer's own role changed, and a 401 that they were
 * signed out: ask for the session too. A 5xx, a 422 or no connection changed
 * nothing, so nothing is fetched.
 */
export function resyncAfter(err: unknown): { family: boolean; session: boolean } {
  const status = isApiError(err) ? err.status : undefined;
  return {
    family: status === 403 || status === 404 || status === 409,
    session: status === 401 || status === 403,
  };
}

/** Shown on the page when the viewer turns out not to be an admin any more. */
export const NO_LONGER_ADMIN =
  "You’re not an admin of this family any more, so the invite link and the member controls are for the admins now.";

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
