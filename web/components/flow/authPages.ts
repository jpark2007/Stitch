// Copy and small decisions for the log in and sign up pages.
//
// No React and only erasable TypeScript, so `node --test` loads this file
// directly. Tests: components/flow/__tests__/authPages.test.mjs.

import { hrefs, safeNext } from "./session";

export type AuthKind = "login" | "signup";

const JOIN_PATH = /^\/join\/[^/?#]+\/?(?:[?#]|$)/;

/**
 * True when `next` is a safe redirect to an invite page, /join/<code>, with a
 * non-empty code (a query or fragment after it is fine).
 */
export function isJoinPath(next: string | null): boolean {
  const safe = safeNext(next);
  return safe !== null && JOIN_PATH.test(safe);
}

const LEDES: Record<AuthKind, { join: string; plain: string }> = {
  login: {
    join: "Log in to join your family.",
    plain: "Pick up your family’s storybook where you left off.",
  },
  signup: {
    join: "Make an account to join your family.",
    plain: "Then start your family’s storybook, or join one you’ve been invited to.",
  },
};

/** The line under the page title: about joining when `next` is an invite page. */
export function authLede(kind: AuthKind, next: string | null): string {
  const copy = LEDES[kind];
  return isJoinPath(next) ? copy.join : copy.plain;
}

// ---------------------------------------------------------------------------
// Signed up, but /me failed
//
// When signup() succeeds the account exists, so a failure of the me() that
// follows must not read as a signup error (pressing Create account again
// would only get a 409). Instead the person goes to log in, with a calm
// notice and their email filled in. The email crosses over in sessionStorage,
// read once and then removed, never in the URL.
//
// Storage can be missing (server render) or throw (private mode, blocked site
// data); none of these helpers ever throw.

/** sessionStorage key for the email handed from sign up to log in. */
export const SIGNUP_HANDOFF_KEY = "stitch.signupHandoff";

/** Shown above the log in form after the handoff. */
export const SIGNUP_READY_NOTICE = "Your account is ready. Log in to carry on.";

type HandoffStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** `storage` as given, or globalThis.sessionStorage when it was left out. */
function resolveSession(storage: HandoffStorage | null | undefined): HandoffStorage | null {
  if (storage !== undefined) return storage;
  try {
    const g = globalThis as { sessionStorage?: Storage };
    return g.sessionStorage ?? null;
  } catch {
    return null;
  }
}

/**
 * After signup() resolved and me() rejected: leave `email` for the log in
 * page (best effort) and return where to go, the log in page carrying the
 * same safe `next`. The email never goes in the returned URL.
 */
export function afterSignupFailure(
  email: string,
  next: string | null,
  storage?: HandoffStorage | null,
): string {
  const value = email.trim();
  if (value) {
    try {
      resolveSession(storage)?.setItem(SIGNUP_HANDOFF_KEY, value);
    } catch {
      // Not handed over: the log in page simply starts blank.
    }
  }
  return hrefs.login(next);
}

/**
 * The email left by afterSignupFailure, or null. Reading it removes it, so it
 * is given out once. Null (never a throw) when storage is missing, empty or
 * throws.
 */
export function readSignupHandoff(storage?: HandoffStorage | null): string | null {
  const store = resolveSession(storage);
  if (!store) return null;
  let value: string | null = null;
  try {
    value = store.getItem(SIGNUP_HANDOFF_KEY);
  } catch {
    return null;
  }
  try {
    store.removeItem(SIGNUP_HANDOFF_KEY);
  } catch {
    // Could not forget it: give nothing out rather than risk giving it twice.
    return null;
  }
  const email = typeof value === "string" ? value.trim() : "";
  return email ? email : null;
}
