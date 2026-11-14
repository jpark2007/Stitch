// Client-side checks for the flow forms (sign up, log in, start a family,
// settings, join, add a memory). Each validator returns a friendly message,
// or null when the value is fine. The server stays the source of truth; these
// only catch the obvious mistakes before a round trip.
//
// Erasable TypeScript only and no imports, so `node --test` can load this file
// directly (see __tests__/validation.test.mjs).

/** Field name -> message. A missing key means the field is fine. */
export type FieldErrors<K extends string> = Partial<Record<K, string>>;

/** Length in characters (code points), the way the API's pydantic counts. */
function charCount(v: string): number {
  return Array.from(v).length;
}

const NAME_MAX = 80;
const PASSWORD_MIN = 8; // api/schemas.py SignupRequest.password min_length
const YEAR_MIN = 1850;

// Something@something.tld with no spaces. Deliberately loose: the server decides.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@.]+$/;

export function validateEmail(v: string): string | null {
  const s = v.trim();
  if (!s) return "Enter your email.";
  if (!EMAIL_RE.test(s)) return "That doesn't look like an email address.";
  return null;
}

/** Passwords are not trimmed: spaces count. */
export function validatePassword(v: string): string | null {
  if (!v) return "Enter a password.";
  if (charCount(v) < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters.`;
  return null;
}

/**
 * A person's name. `what` names the thing in the message, e.g. "your name"
 * (the default) or "their name".
 */
export function validateName(v: string, what: string = "your name"): string | null {
  const s = v.trim();
  if (!s) return `Enter ${what}.`;
  if (charCount(s) > NAME_MAX) return `Keep ${what} to ${NAME_MAX} characters or fewer.`;
  return null;
}

export function validateFamilyName(v: string): string | null {
  const s = v.trim();
  if (!s) return "Give your family a name.";
  if (charCount(s) > NAME_MAX) return `Keep the family name to ${NAME_MAX} characters or fewer.`;
  return null;
}

/**
 * A four-digit-ish year from a text or number input. Empty is fine unless
 * `required`. Bounds default to 1850 through the current year, inclusive.
 */
export function validateYear(
  v: string | number,
  opts: { min?: number; max?: number; required?: boolean } = {},
): string | null {
  const thisYear = new Date().getFullYear();
  const min = opts.min ?? YEAR_MIN;
  const max = opts.max ?? thisYear;
  const required = opts.required ?? false;

  let n: number;
  if (typeof v === "number") {
    if (Number.isNaN(v)) return required ? "Enter a year." : null; // an empty <input type=number> reads as NaN
    n = v;
  } else {
    const s = v.trim();
    if (!s) return required ? "Enter a year." : null;
    if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(s)) return "Enter the year as a number, like 1987.";
    n = Number(s);
  }

  if (!Number.isFinite(n)) return "Enter the year as a number, like 1987.";
  if (!Number.isInteger(n)) return "Use a whole year, like 1987.";
  if (n < min) return `Pick a year from ${min} on.`;
  if (n > max) {
    return n > thisYear ? "That year hasn't happened yet." : `Pick a year no later than ${max}.`;
  }
  return null;
}

/** True when any value is a non-empty message. */
export function hasErrors(e: Record<string, string | null | undefined>): boolean {
  return Object.values(e).some((msg) => typeof msg === "string" && msg.length > 0);
}

/** "participant_ids" and "participantIds" both become "participantids". */
function looseKey(k: string): string {
  return k.replace(/[_\-\s]/g, "").toLowerCase();
}

/**
 * Put server-side field messages (ApiError.fieldErrors) onto the form's known
 * fields, overriding any local message for the same field. A server key
 * matches a field exactly, by its last dotted segment ("person.name" ->
 * "name"), or ignoring case and underscores ("participant_ids" ->
 * "participantIds"). Messages for keys that match no field are joined into
 * `rest`, for a form-level FormError; `rest` is null when there are none.
 */
export function mergeServerErrors<K extends string>(
  local: FieldErrors<K>,
  server: Record<string, string>,
  fields: readonly K[],
): { fields: FieldErrors<K>; rest: string | null } {
  const merged: FieldErrors<K> = { ...local };
  const leftovers: string[] = [];

  const find = (key: string): K | undefined => {
    const exact = fields.find((f) => f === key);
    if (exact !== undefined) return exact;
    const last = key.split(".").pop() ?? key;
    const loose = looseKey(last);
    return fields.find((f) => f === last) ?? fields.find((f) => looseKey(f) === loose);
  };

  for (const [key, msg] of Object.entries(server)) {
    if (typeof msg !== "string" || !msg.trim()) continue;
    const field = find(key);
    if (field !== undefined) merged[field] = msg;
    else if (!leftovers.includes(msg)) leftovers.push(msg);
  }

  return { fields: merged, rest: leftovers.length ? leftovers.join(" ") : null };
}
