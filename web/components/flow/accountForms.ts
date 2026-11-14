// Pure logic behind the account forms: log in, sign up, start a family, and
// the family switcher. The drafts they edit, their checks, the request bodies
// they hand to the page, and how a server error lands on their fields.
//
// Erasable TypeScript only. The one runtime import (./validation) is
// extensionless for the Next build; __tests__/accountForms.test.mjs adds a
// resolve hook so `node --test` finds it.
import type { CreateFamilyRequest, FamilySummary, LoginRequest, SignupRequest } from "@/lib/types";
import {
  mergeServerErrors,
  validateEmail,
  validateFamilyName,
  validateName,
  validatePassword,
  validateYear,
  type FieldErrors,
} from "./validation";

// ---------------------------------------------------------------------------
// Drafts and fields

export interface LoginDraft {
  email: string;
  password: string;
}

export interface SignupDraft {
  name: string;
  email: string;
  password: string;
}

/** `born` stays a string (from YearInput) until it is sent. */
export interface FamilyDraft {
  familyName: string;
  yourName: string;
  born: string;
}

export type LoginField = "email" | "password";
export type SignupField = "name" | "email" | "password";
export type FamilyField = "familyName" | "yourName" | "born";

/** Every field, in form order, for mergeServerErrors and focus order. */
export const LOGIN_FIELDS: readonly LoginField[] = ["email", "password"];
export const SIGNUP_FIELDS: readonly SignupField[] = ["name", "email", "password"];
export const FAMILY_FIELDS: readonly FamilyField[] = ["familyName", "yourName", "born"];

/**
 * The password rule as a hint, taken from validatePassword itself so the hint
 * and the check can never disagree ("Use at least 8 characters.").
 */
export const PASSWORD_HINT: string = validatePassword("x") ?? "";

// ---------------------------------------------------------------------------
// Validators

/** Log in: a real-looking email and any non-empty password (no length rule). */
export function validateLogin(d: LoginDraft): FieldErrors<LoginField> {
  const errors: FieldErrors<LoginField> = {};
  const email = validateEmail(d.email);
  if (email) errors.email = email;
  if (!d.password) errors.password = "Enter your password.";
  return errors;
}

export function validateSignup(d: SignupDraft): FieldErrors<SignupField> {
  const errors: FieldErrors<SignupField> = {};
  const name = validateName(d.name);
  if (name) errors.name = name;
  const email = validateEmail(d.email);
  if (email) errors.email = email;
  const password = validatePassword(d.password);
  if (password) errors.password = password;
  return errors;
}

/** The year you were born is optional; when given it must be a sensible year. */
export function validateFamily(d: FamilyDraft): FieldErrors<FamilyField> {
  const errors: FieldErrors<FamilyField> = {};
  const familyName = validateFamilyName(d.familyName);
  if (familyName) errors.familyName = familyName;
  const yourName = validateName(d.yourName);
  if (yourName) errors.yourName = yourName;
  const born = validateYear(d.born);
  if (born) errors.born = born;
  return errors;
}

// ---------------------------------------------------------------------------
// Request bodies

/** The email is trimmed; the password is sent exactly as typed. */
export function toLoginRequest(d: LoginDraft): LoginRequest {
  return { email: d.email.trim(), password: d.password };
}

/** Name and email trimmed; the password is sent exactly as typed. */
export function toSignupRequest(d: SignupDraft): SignupRequest {
  return { name: d.name.trim(), email: d.email.trim(), password: d.password };
}

// ---------------------------------------------------------------------------
// After a successful submit
//
// With cacheComponents on, a page left behind stays mounted (hidden by React
// Activity) with its state and DOM. The log in and sign up forms swap in
// these drafts once they are sent, so a hidden form never holds a password.

/**
 * The log in draft once it has been sent: the password is gone, the email is
 * kept. The email is not a secret, the hidden form only lives as long as this
 * signed-in session (signing out reloads the whole document), and coming back
 * to /login in the same session finds your email ready.
 */
export function loginDraftAfterSuccess(d: LoginDraft): LoginDraft {
  return { email: d.email, password: "" };
}

/** The sign up draft once it has been sent: empty, ready for nobody in particular. */
export function signupDraftAfterSuccess(): SignupDraft {
  return { name: "", email: "", password: "" };
}

/** The family and its first person: you. A blank year is sent as null. */
export function toCreateFamilyRequest(d: FamilyDraft): CreateFamilyRequest {
  const born = d.born.trim();
  return {
    name: d.familyName.trim(),
    person: { name: d.yourName.trim(), born: born ? Number(born) : null },
  };
}

// ---------------------------------------------------------------------------
// Server errors

/**
 * Create-family field names as the API may report them, to the form's fields.
 * The client keeps only the last segment of a 422 `loc`, so a bare "name" is
 * the family's name (the top-level field); "person.name" is yours.
 */
const FAMILY_SERVER_KEYS: Record<string, FamilyField> = {
  name: "familyName",
  familyname: "familyName",
  "person.name": "yourName",
  yourname: "yourName",
  born: "born",
  "person.born": "born",
};

/** Rename create-family server keys to the form's fields; others pass through. */
export function familyServerErrors(server: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  const lookup = (k: string): FamilyField | undefined =>
    Object.hasOwn(FAMILY_SERVER_KEYS, k) ? FAMILY_SERVER_KEYS[k] : undefined;
  for (const [key, msg] of Object.entries(server)) {
    const field = lookup(key) ?? lookup(key.toLowerCase()) ?? key;
    if (!Object.hasOwn(out, field)) out[field] = msg;
  }
  return out;
}

/**
 * What the form shows after the server says no: each field message on its
 * field (through mergeServerErrors), and one form-level message made of the
 * error's detail plus any field messages that matched no field.
 */
export function serverErrorState<K extends string>(
  err: { detail: string; fieldErrors: Record<string, string> },
  fields: readonly K[],
): { fields: FieldErrors<K>; formError: string | null } {
  const merged = mergeServerErrors<K>({}, err.fieldErrors, fields);
  const parts: string[] = [];
  for (const msg of [err.detail, merged.rest]) {
    const s = msg?.trim();
    if (s && !parts.includes(s)) parts.push(s);
  }
  return { fields: merged.fields, formError: parts.length ? parts.join(" ") : null };
}

/** A copy of `errors` without `key`, or `errors` itself when it has none. */
export function withoutError<K extends string>(errors: FieldErrors<K>, key: K): FieldErrors<K> {
  if (!(key in errors)) return errors;
  const next = { ...errors };
  delete next[key];
  return next;
}

// ---------------------------------------------------------------------------
// Family switcher

/** The switcher only appears for someone in two or more families. */
export function shouldShowSwitcher(families: readonly FamilySummary[] | null | undefined): boolean {
  return Array.isArray(families) && families.length >= 2;
}

export function roleLabel(role: FamilySummary["role"]): string {
  return role === "admin" ? "Admin" : "Member";
}
