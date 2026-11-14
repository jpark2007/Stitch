// Session plumbing shared by the flow pages: where each page lives, where to
// go after signing in, which family is "current", and remembering that family
// between visits.
//
// No runtime imports and only erasable TypeScript, so `node --test` loads this
// file directly. Tests: components/flow/__tests__/session.test.mjs.

import type { FamilySummary, MeResponse } from "@/lib/types";

/** localStorage key for the family the user last worked in. */
export const FAMILY_KEY = "stitch.familyId";

const enc = encodeURIComponent;

/** A safe `?next=` suffix, or nothing. */
function nextQuery(next: string | null | undefined): string {
  const safe = safeNext(next);
  return safe === null ? "" : `?next=${enc(safe)}`;
}

/** Every page the flow links to or redirects to. Ids are URL-encoded. */
export const hrefs = {
  home: (): string => "/",
  login: (next?: string | null): string => `/login${nextQuery(next)}`,
  signup: (next?: string | null): string => `/signup${nextQuery(next)}`,
  newFamily: (): string => "/families/new",
  settings: (fid: string): string => `/families/${enc(fid)}/settings`,
  join: (code: string): string => `/join/${enc(code)}`,
  add: (o?: { familyId?: string | null; memoryId?: string | null }): string => {
    const params: string[] = [];
    if (o?.familyId) params.push(`family=${enc(o.familyId)}`);
    if (o?.memoryId) params.push(`memoryId=${enc(o.memoryId)}`);
    return params.length ? `/add?${params.join("&")}` : "/add";
  },
  /** A moment's book page; with a family id, `?family=` tells it which family to load. */
  memory: (id: string, familyId?: string | null): string => `/memories/${enc(id)}${familyQuery(familyId)}`,
  /** A person's book; with a family id, `?family=` tells it which family to load. */
  person: (id: string, familyId?: string | null): string => `/people/${enc(id)}${familyQuery(familyId)}`,
};

/** A `?family=` suffix for a non-empty family id, or nothing. */
function familyQuery(familyId: string | null | undefined): string {
  return typeof familyId === "string" && familyId !== "" ? `?family=${enc(familyId)}` : "";
}

// Pages a `next` must never point back to: it would loop through sign-in.
const AUTH_PATHS = ["/login", "/signup"];
// C0 controls, DEL and C1 controls. Browsers drop tabs and newlines from URLs,
// so "/\t/evil.example" would otherwise become "//evil.example".
const CONTROL_CHARS = /[\u0000-\u001f\u007f-\u009f]/;
const MAX_NEXT_LENGTH = 2048;
const PROBE_ORIGIN = "http://stitch.invalid";

/**
 * `raw` when it is a same-origin path that is safe to redirect to after
 * signing in, else null. It must start with a single "/" (not "//" or a
 * backslash, which browsers read as "/"), carry no scheme or control
 * characters, and not be the login or signup page itself.
 */
export function safeNext(raw: string | null | undefined): string | null {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > MAX_NEXT_LENGTH) return null;
  if (raw[0] !== "/" || raw[1] === "/" || raw.includes("\\")) return null;
  if (CONTROL_CHARS.test(raw)) return null;
  let url: URL;
  try {
    url = new URL(raw, PROBE_ORIGIN);
  } catch {
    return null;
  }
  // Belt and braces: whatever the parser makes of it must stay on this origin.
  if (url.origin !== PROBE_ORIGIN) return null;
  const path = url.pathname.replace(/\/+$/, "").toLowerCase() || "/";
  if (AUTH_PATHS.includes(path)) return null;
  return raw;
}

/**
 * Where to send someone who just signed up or logged in: the page they were
 * headed for, else starting a family when they have none, else home.
 */
export function destinationAfterAuth(me: MeResponse, next: string | null): string {
  const safe = safeNext(next);
  if (safe !== null) return safe;
  return me.families.length === 0 ? hrefs.newFamily() : hrefs.home();
}

/** The preferred family when the user belongs to it, else their first, else null. */
export function pickFamily(
  families: FamilySummary[],
  preferred: string | null | undefined,
): FamilySummary | null {
  if (preferred) {
    const match = families.find((f) => f.id === preferred);
    if (match) return match;
  }
  return families[0] ?? null;
}

// ---------------------------------------------------------------------------
// Remembering the current family. Storage can be missing (server render) or
// throw (private mode, blocked site data); none of these helpers ever throw.

/** `storage` as given, or globalThis.localStorage when it was left out. */
function resolveStorage(storage: Storage | null | undefined): Storage | null {
  if (storage !== undefined) return storage;
  try {
    const g = globalThis as { localStorage?: Storage };
    return g.localStorage ?? null;
  } catch {
    return null;
  }
}

/** Remember `fid` as the family to open next time. */
export function rememberFamily(fid: string, storage?: Storage | null): void {
  if (!fid) return;
  try {
    resolveStorage(storage)?.setItem(FAMILY_KEY, fid);
  } catch {
    // Not remembered; pickFamily falls back to the first family.
  }
}

/** The remembered family id, or null. */
export function recallFamily(storage?: Storage | null): string | null {
  try {
    const value = resolveStorage(storage)?.getItem(FAMILY_KEY);
    return typeof value === "string" && value ? value : null;
  } catch {
    return null;
  }
}

/** Forget the remembered family (on sign-out). */
export function forgetFamily(storage?: Storage | null): void {
  try {
    resolveStorage(storage)?.removeItem(FAMILY_KEY);
  } catch {
    // Nothing to forget, or storage is unavailable.
  }
}

// ---------------------------------------------------------------------------
// "Probably signed in". The session cookie is httpOnly, so the page cannot see
// it. This hint is set whenever the server confirms a session (log in, sign
// up, a SessionGate's /me) and cleared on sign-out or a 401. The log in and
// sign up pages ask /me on arrival only when it is set: a signed-in visitor is
// sent on, and a signed-out one makes no request at all. It is only a hint;
// the server's answer always decides.

/** localStorage key for the signed-in hint. */
export const SIGNED_IN_KEY = "stitch.signedIn";

/** Note that the server just confirmed a session. */
export function rememberSignedIn(storage?: Storage | null): void {
  try {
    resolveStorage(storage)?.setItem(SIGNED_IN_KEY, "1");
  } catch {
    // Not noted: the log in page simply shows its form.
  }
}

/** True when a session was confirmed in this browser and not since ended. */
export function recallSignedIn(storage?: Storage | null): boolean {
  try {
    return resolveStorage(storage)?.getItem(SIGNED_IN_KEY) === "1";
  } catch {
    return false;
  }
}

/** Drop the signed-in hint (on sign-out, or when the server says 401). */
export function forgetSignedIn(storage?: Storage | null): void {
  try {
    resolveStorage(storage)?.removeItem(SIGNED_IN_KEY);
  } catch {
    // Nothing to forget, or storage is unavailable.
  }
}
