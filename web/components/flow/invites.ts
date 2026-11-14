// Pure helpers behind the invite link card on family settings: the shareable
// URL, which invite is the live one, and how long it has left. Invite codes
// are random, revocable and expire after 14 days (ARCHITECTURE.md section 4).
//
// Erasable TypeScript with type-only imports, so `node --test` can load this
// file directly (see __tests__/settingsParts.test.mjs).
import type { Invite } from "../../lib/types";

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * What a failed invite action came to, in plain words. Turning off a link
 * the server no longer has (404, "Invite not found or expired") is what the
 * admin wanted: `done` is true and `message` says so calmly. A 403 is a
 * viewer who is no longer an admin, a 404 on making a link a family that is
 * gone; anything else (a 5xx, no connection) keeps the error's own message,
 * which the API client already words for people.
 */
export function inviteActionOutcome(err: unknown, action: "create" | "revoke"): { done: boolean; message: string } {
  const raw = typeof err === "object" && err !== null ? (err as { status?: unknown }).status : undefined;
  const status = typeof raw === "number" ? raw : undefined;
  if (status === 403) {
    const what = action === "create" ? "make invite links" : "turn off invite links";
    return { done: false, message: `Only admins can ${what}, and you’re not an admin of this family any more.` };
  }
  if (status === 404) {
    return action === "revoke"
      ? { done: true, message: "That link was already turned off." }
      : { done: false, message: "We couldn’t find this family any more." };
  }
  const own = err instanceof Error && err.message.trim() ? err.message : "Something went wrong. Please try again.";
  return { done: false, message: own };
}

/** The link people open to join: `${origin}/join/${code}`. */
export function inviteUrl(origin: string, code: string): string {
  return `${origin.replace(/\/+$/, "")}/join/${encodeURIComponent(code)}`;
}

/** Milliseconds since the epoch, or null when the string isn't a date. */
function timeOf(iso: string | null | undefined): number | null {
  if (typeof iso !== "string" || !iso.trim()) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

/**
 * The invite to show: not revoked, still in the future, and the latest to
 * expire among those. Invites with unreadable dates are ignored. Null when
 * there is none (or `invites` is missing).
 */
export function pickActiveInvite(invites: Invite[] | undefined, now: Date): Invite | null {
  if (!Array.isArray(invites)) return null;
  const nowMs = now.getTime();
  let best: Invite | null = null;
  let bestMs = -Infinity;
  for (const invite of invites) {
    if (!invite || invite.revoked) continue;
    const t = timeOf(invite.expiresAt);
    if (t === null || t <= nowMs) continue;
    if (t > bestMs) {
      best = invite;
      bestMs = t;
    }
  }
  return best;
}

/** Whole calendar days from `now` to `then`, in local time (DST-safe). */
function calendarDaysBetween(now: Date, then: Date): number {
  const a = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const b = Date.UTC(then.getFullYear(), then.getMonth(), then.getDate());
  return Math.round((b - a) / DAY_MS);
}

/**
 * How long an invite has left, in words: "Works for 13 more days",
 * "Expires tomorrow", "Expires today" or "Expired". Days are whole calendar
 * days in local time, so an invite ending at 9am tomorrow "Expires tomorrow"
 * even at 11pm today. Returns "" when the date can't be read.
 */
export function describeExpiry(expiresAt: string, now: Date): string {
  const t = timeOf(expiresAt);
  const nowMs = now.getTime();
  if (t === null || !Number.isFinite(nowMs)) return "";
  if (t <= nowMs) return "Expired";
  const days = calendarDaysBetween(now, new Date(t));
  if (days <= 0) return "Expires today";
  if (days === 1) return "Expires tomorrow";
  return `Works for ${days} more days`;
}
