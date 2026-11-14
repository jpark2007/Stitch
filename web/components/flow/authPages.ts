// Copy and small decisions for the log in and sign up pages.
//
// No React and only erasable TypeScript, so `node --test` loads this file
// directly. Tests: components/flow/__tests__/authPages.test.mjs.

import { safeNext } from "./session";

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
