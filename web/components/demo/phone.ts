// The texting half of the demo control page: the phone number check, the
// preferences an enrollment is made with, what each contact status and
// Channels error says, the pairing code's expiry, and the "send it now" call
// (POST /families/{fid}/channels/people/{person_id}/prompt-now), which may
// not exist on a server yet.
//
// No runtime imports and only erasable TypeScript, so `node --test` loads this
// file directly. Tests: components/demo/__tests__/phone.test.mjs.

/** E.164: "+", then 8 to 15 digits, no leading zero (as the Channels settings routes accept). */
const E164 = /^\+[1-9]\d{7,14}$/;

/**
 * The number as E.164, or null when it is not one. Spaces, dashes, dots and
 * brackets are dropped first ("+1 (555) 555-0100" reads as "+15555550100");
 * the country code is never guessed.
 */
export function normalizePhone(raw: string): string | null {
  const compact = raw.trim().replace(/[\s\-.() ]/g, "");
  return E164.test(compact) ? compact : null;
}

export const PHONE_HINT = "With the country code, such as +1 555 555 0100.";
export const PHONE_ERROR = "Enter the number with its country code, such as +15555550100.";

/** The quiet hours the settings page starts from (components/channels/settings.ts preferencesFor). */
export const DEFAULT_QUIET_HOURS = { start: "21:00", end: "08:00" } as const;

/** Preferences for a new enrollment: this browser's time zone, weekly, the default quiet hours. */
export function demoPreferences(timezone: string | null | undefined): {
  timezone: string;
  cadenceDays: number;
  quietHours: { start: string; end: string };
} {
  const tz = typeof timezone === "string" && timezone.trim() ? timezone.trim() : "America/New_York";
  return { timezone: tz, cadenceDays: 7, quietHours: { ...DEFAULT_QUIET_HOURS } };
}

export type ContactStatus = "none" | "pending" | "active" | "stopped";

/** The short status label and one line on what it means, for the operator. */
export function statusCopy(status: ContactStatus): { label: string; line: string } {
  switch (status) {
    case "none":
      return { label: "Not paired", line: "No phone is paired for this person yet." };
    case "pending":
      return { label: "Waiting for the code", line: "The phone must text the code below to opt in. Nothing has been sent to it." };
    case "active":
      return { label: "Paired", line: "This phone has opted in. It gets the question each week and can reply STOP at any time." };
    case "stopped":
      return { label: "Stopped", line: "Texting is stopped for this phone. Pairing again needs a fresh code from the phone." };
  }
}

/**
 * What a failed Channels settings call says. `fallback` is the settings
 * client's own message (ChannelSettingsError.message), used for statuses
 * with nothing more specific to say here.
 */
export function channelErrorCopy(status: number, fallback: string, personName: string, hasAccount: boolean): string {
  switch (status) {
    case 0:
      return "The server didn’t answer. Check the connection and try again.";
    case 401:
      return "Your session has ended. Sign in again in this window.";
    case 403:
      return hasAccount
        ? `Only ${personName} can pair their own phone, signed in as themselves.`
        : `${personName} has no account, so only a family admin can pair their phone.`;
    case 409:
      return "That phone can’t be paired right now: it is already paired, or a code for it is still waiting (codes last 10 minutes).";
    case 422:
      return "The server refused that number or the time zone. Check the number and try again.";
    case 429:
      return "Too many pairing attempts this hour. Wait a little before asking for another code.";
    default:
      return fallback;
  }
}

/** The one line shown instead of the texting section when Channels answers 404 (it is off on this server). */
export const TEXTING_OFF = "Texting is off on this server, so phones can’t be paired or sent questions here.";

/** "Expires 3:42 PM, in 9 min" style copy for a pairing code; "Expired" once past. */
export function expiryCopy(expiresAt: string, now: number, timeZone?: string): { expired: boolean; text: string } {
  const at = Date.parse(expiresAt);
  if (!Number.isFinite(at)) return { expired: false, text: "" };
  const left = at - now;
  if (left <= 0) return { expired: true, text: "This code has expired. Ask for a new one." };
  const clock = new Date(at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone });
  const mins = Math.floor(left / 60_000);
  const secs = Math.floor((left % 60_000) / 1000);
  const remaining = mins >= 1 ? `${mins} min ${String(secs).padStart(2, "0")} s` : `${secs} s`;
  return { expired: false, text: `Expires at ${clock} (${remaining} left)` };
}

// --- Send it now -------------------------------------------------------------

export type PromptNowResult =
  | { kind: "sent" }
  | { kind: "blocked"; reason: string }
  | { kind: "unavailable" }
  | { kind: "error"; message: string; status: number };

export const PROMPT_NOW_UNAVAILABLE =
  "Sending on demand isn’t switched on for this server yet; the phone gets the question on its weekly schedule.";

function promptNowUrl(baseUrl: string, fid: string, personId: string): string {
  return `${baseUrl.replace(/\/+$/, "")}/families/${encodeURIComponent(fid)}/channels/people/${encodeURIComponent(personId)}/prompt-now`;
}

async function detailOf(res: Response): Promise<string | null> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && typeof (body as { detail?: unknown }).detail === "string") {
      return (body as { detail: string }).detail;
    }
  } catch {
    // No JSON body.
  }
  return null;
}

/** Read a prompt-now reply. 404 and 405 mean the route is not on this server. */
export async function readPromptNow(res: Response): Promise<PromptNowResult> {
  if (res.status === 404 || res.status === 405) return { kind: "unavailable" };
  if (!res.ok) {
    const detail = await detailOf(res);
    const message =
      res.status === 401
        ? "Your session has ended. Sign in again in this window."
        : res.status === 403
          ? "You can’t send this person’s question from this account."
          : res.status === 409
            ? detail ?? "This phone isn’t paired and opted in yet."
            : res.status === 429
              ? detail ?? "Sent too recently. Wait a little and try again."
              : detail ?? `The server said no (${res.status}).`;
    return { kind: "error", message, status: res.status };
  }
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return { kind: "error", message: "The server’s answer couldn’t be read.", status: 0 };
  }
  const o = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  if (o.status === "sent") return { kind: "sent" };
  if (o.status === "blocked") {
    const reason = typeof o.reason === "string" && o.reason.trim() ? o.reason.trim() : "No reason given.";
    return { kind: "blocked", reason };
  }
  return { kind: "error", message: "The server’s answer couldn’t be read.", status: 0 };
}

/** POST prompt-now with `{}`. */
export async function promptNow(
  baseUrl: string,
  fid: string,
  personId: string,
  fetcher: typeof fetch = fetch,
): Promise<PromptNowResult> {
  let res: Response;
  try {
    res = await fetcher(promptNowUrl(baseUrl, fid, personId), {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch {
    return { kind: "error", message: "The server didn’t answer. Check the connection and try again.", status: 0 };
  }
  return readPromptNow(res);
}

/**
 * Whether the server has the prompt-now route, without sending anything: a
 * GET of a POST-only route answers 405 when the route exists and 404 when it
 * does not. Anything else (or no answer) is "unknown": the button stays on
 * and the POST itself decides.
 */
export async function probePromptNow(
  baseUrl: string,
  fid: string,
  personId: string,
  fetcher: typeof fetch = fetch,
): Promise<"available" | "unavailable" | "unknown"> {
  try {
    const res = await fetcher(promptNowUrl(baseUrl, fid, personId), {
      method: "GET",
      credentials: "include",
      cache: "no-store",
    });
    if (res.status === 404) return "unavailable";
    if (res.status === 405) return "available";
    return "unknown";
  } catch {
    return "unknown";
  }
}
