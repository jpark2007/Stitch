// Pure helpers behind the invite link card on family settings: the shareable
// URL, which invite is the live one, and how long it has left. Invite codes
// are random, revocable and expire after 14 days (ARCHITECTURE.md section 4).
//
// Erasable TypeScript with type-only imports, so `node --test` can load this
// file directly (see __tests__/settingsParts.test.mjs).
import type { Invite } from "../../lib/types";

const DAY_MS = 24 * 60 * 60 * 1000;

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
