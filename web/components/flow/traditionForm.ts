// Pure logic behind the "Add a tradition" form: the choices it offers, the
// draft it edits, its checks (the bounds of api/schemas.py
// CreateTraditionRequest), the request it sends, and where a server error
// lands. Erasable TypeScript only.
// The one runtime import (./validation) is extensionless for the Next build;
// __tests__/traditionForm.test.mjs adds a resolve hook so `node --test` finds it.
import type { TraditionDraftPage } from "@/components/traditions/traditionBook";
import type { CreateTraditionRequest, TraditionWhen } from "@/lib/types";
import { mergeServerErrors, validateYear, type FieldErrors } from "./validation";

export type TraditionCategory = CreateTraditionRequest["category"];
export type TraditionWhenKind = TraditionWhen["kind"];

/** The eight categories, in the order the form shows them, with friendly names. */
export const CATEGORY_OPTIONS: ReadonlyArray<{ value: TraditionCategory; label: string }> = [
  { value: "food", label: "Food" },
  { value: "holiday", label: "Holiday" },
  { value: "trip", label: "Trip" },
  { value: "music", label: "Music" },
  { value: "game", label: "Game" },
  { value: "faith", label: "Faith" },
  { value: "craft", label: "Craft" },
  { value: "other", label: "Something else" },
];

/** How often it happens, in the order the form shows them. */
export const WHEN_OPTIONS: ReadonlyArray<{ value: TraditionWhenKind; label: string }> = [
  { value: "yearly", label: "Every year" },
  { value: "seasonal", label: "Every season" },
  { value: "monthly", label: "Every month" },
  { value: "weekly", label: "Every week" },
  { value: "occasional", label: "Now and then" },
];

export const MONTH_NAMES: readonly string[] = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

// Bounds, mirroring api/schemas.py CreateTraditionRequest / TraditionWhen.
/** Longest name, in characters (code points) after trimming. */
export const TITLE_MAX = 200;
/** Longest story, in characters (code points) after trimming. */
export const DESCRIPTION_MAX = 5000;
/** Longest "when, in your words" label, in characters (code points) after trimming. */
export const LABEL_MAX = 100;
/** Most people a tradition can name. */
export const PEOPLE_MAX = 100;
/** Most photos a tradition can hold. */
export const PHOTOS_MAX = 10;
/** Earliest year a tradition can have started. */
export const START_YEAR_MIN = 1800;

/** A placeholder for the free "when" label, to fit how often it happens. */
export function labelPlaceholder(kind: TraditionWhenKind | null): string {
  switch (kind) {
    case "yearly":
      return "Christmas Eve";
    case "seasonal":
      return "Every summer";
    case "monthly":
      return "The first Saturday";
    case "weekly":
      return "Every Sunday";
    case "occasional":
      return "Whenever we're all home";
    default:
      return "Christmas Eve";
  }
}

/**
 * Whether picking a month makes sense for how often it happens: a yearly
 * tradition falls in a month, and so does a season's. A monthly, weekly or
 * now-and-then one does not, so the form hides the month and never sends it.
 */
export function monthMakesSense(kind: TraditionWhenKind | null): boolean {
  return kind === "yearly" || kind === "seasonal";
}

/** What the form edits. Numbers stay strings until sent; "" is "not given". */
export interface TraditionDraft {
  title: string;
  description: string;
  category: TraditionCategory | null;
  kind: TraditionWhenKind | null;
  label: string;
  /** "" or "1".."12". */
  month: string;
  participantIds: string[];
  startedYear: string;
}

export type TraditionField =
  | "title"
  | "description"
  | "category"
  | "kind"
  | "label"
  | "month"
  | "participantIds"
  | "photos"
  | "startedYear";

/** Every field, in form order, for mapping server errors. */
export const TRADITION_FIELDS: readonly TraditionField[] = [
  "title",
  "category",
  "kind",
  "label",
  "month",
  "description",
  "participantIds",
  "photos",
  "startedYear",
];

function charCount(v: string): number {
  return Array.from(v).length;
}

const countFormat = new Intl.NumberFormat("en-US");

/** Order-keeping dedupe that also drops blank ids. */
function uniqueIds(ids: readonly string[]): string[] {
  const out: string[] = [];
  for (const id of ids) {
    if (typeof id === "string" && id && !out.includes(id)) out.push(id);
  }
  return out;
}

/**
 * A fresh draft. The person adding it starts ticked under "Who takes part?"
 * (they can untick themselves); someone not in the tree yet starts with nobody.
 */
export function emptyTraditionDraft(selfPersonId?: string | null): TraditionDraft {
  return {
    title: "",
    description: "",
    category: null,
    kind: null,
    label: "",
    month: "",
    participantIds: selfPersonId ? [selfPersonId] : [],
    startedYear: "",
  };
}

/** The month as a number 1..12, or null for none or anything else. */
function monthNumber(v: string): number | null {
  const s = v.trim();
  if (!/^\d{1,2}$/.test(s)) return null;
  const n = Number(s);
  return n >= 1 && n <= 12 ? n : null;
}

/**
 * Friendly messages for what is wrong before sending, checking the same
 * bounds the API does. `photoCount` is how many photos are attached.
 */
export function validateTraditionDraft(d: TraditionDraft, photoCount = 0): FieldErrors<TraditionField> {
  const errors: FieldErrors<TraditionField> = {};

  const title = d.title.trim();
  if (!title) errors.title = "Give the tradition a name.";
  else if (charCount(title) > TITLE_MAX) errors.title = `Keep the name to ${TITLE_MAX} characters or fewer.`;

  if (!d.category) errors.category = "Pick what kind of tradition it is.";
  if (!d.kind) errors.kind = "Pick how often it happens.";

  if (charCount(d.label.trim()) > LABEL_MAX) {
    errors.label = `Keep this to ${LABEL_MAX} characters or fewer.`;
  }
  if (monthMakesSense(d.kind) && d.month.trim() && monthNumber(d.month) === null) {
    errors.month = "Pick a month from the list.";
  }

  if (charCount(d.description.trim()) > DESCRIPTION_MAX) {
    errors.description = `Keep the story to ${countFormat.format(DESCRIPTION_MAX)} characters or fewer.`;
  }

  if (uniqueIds(d.participantIds).length > PEOPLE_MAX) {
    errors.participantIds = `Pick up to ${PEOPLE_MAX} people.`;
  }
  if (photoCount > PHOTOS_MAX) errors.photos = `A tradition can hold up to ${PHOTOS_MAX} photos.`;

  const year = validateYear(d.startedYear, { min: START_YEAR_MIN });
  if (year) errors.startedYear = year;

  return errors;
}

/**
 * The body for api.createTradition. Call only with a draft that passed
 * validateTraditionDraft. Empty optional fields are left out; the month is
 * sent only when it makes sense for how often it happens.
 */
export function toCreateTraditionRequest(d: TraditionDraft, photoIds: readonly string[] = []): CreateTraditionRequest {
  if (!d.category || !d.kind) throw new Error("toCreateTraditionRequest: category and kind must be picked first");
  const when: TraditionWhen = { kind: d.kind };
  const label = d.label.trim();
  if (label) when.label = label;
  const month = monthMakesSense(d.kind) ? monthNumber(d.month) : null;
  if (month !== null) when.month = month;

  const body: CreateTraditionRequest = { title: d.title.trim(), category: d.category, when };
  const description = d.description.trim();
  if (description) body.description = description;
  const people = uniqueIds(d.participantIds);
  if (people.length) body.participantIds = people;
  const photos = uniqueIds(photoIds);
  if (photos.length) body.photoIds = photos;
  const year = d.startedYear.trim();
  if (year) body.startedYear = Number(year);
  return body;
}

// ---------------------------------------------------------------------------
// The page in the book beside the form

/**
 * Whether the form still holds only what it started with (the author ticked
 * under "Who takes part?", nothing else): the book beside it shows a blank
 * page, not a snapshot of the author alone.
 */
export function traditionDraftIsBlank(d: TraditionDraft, selfPersonId: string | null, photoCount = 0): boolean {
  const start = selfPersonId ? [selfPersonId] : [];
  const people = uniqueIds(d.participantIds);
  return (
    !d.title.trim() &&
    !d.description.trim() &&
    !d.category &&
    !d.kind &&
    !d.label.trim() &&
    !d.startedYear.trim() &&
    photoCount === 0 &&
    people.length === start.length &&
    people.every((id, i) => id === start[i])
  );
}

/**
 * The draft as the book's builder reads a tradition being written
 * (traditionBook.ts draftSpread): trimmed as it will be sent, a month only
 * where one makes sense, a year only once it is a whole, valid one (not "19"
 * on the way to "1962"), and nobody keeping it while the form is blank.
 * `photoIds` name the photos for the builder's photoUrl.
 */
export function traditionDraftPage(
  d: TraditionDraft,
  selfPersonId: string | null,
  photoIds: readonly string[] = [],
): TraditionDraftPage {
  const year = d.startedYear.trim();
  const blank = traditionDraftIsBlank(d, selfPersonId, photoIds.length);
  return {
    title: d.title.trim(),
    description: d.description.trim(),
    category: d.category,
    when: {
      kind: d.kind,
      label: d.label.trim() || null,
      month: monthMakesSense(d.kind) ? monthNumber(d.month) : null,
    },
    participantIds: blank ? [] : uniqueIds(d.participantIds),
    photoIds,
    startedYear: /^\d{4}$/.test(year) && !validateYear(year, { min: START_YEAR_MIN }) ? Number(year) : null,
  };
}

// ---------------------------------------------------------------------------
// When sending fails

/** The request's field names that are not the form's own (lib/api.ts keys by a loc's last segment). */
const SERVER_ALIASES: Record<string, TraditionField> = {
  photoIds: "photos",
  photo_ids: "photos",
  // A `when` that is not an object at all.
  when: "kind",
};

/**
 * Put ApiError.fieldErrors from a 422 onto the form's fields. The client keys
 * them by a loc's last segment ("body.when.label" -> "label"); photoIds lands
 * on the photos field. Anything that names no field comes back as `rest`.
 */
export function traditionServerErrors(server: Record<string, string> | null | undefined): {
  fields: FieldErrors<TraditionField>;
  rest: string | null;
} {
  const renamed: Record<string, string> = {};
  for (const [key, msg] of Object.entries(server ?? {})) {
    const last = key.split(".").pop() ?? key;
    const alias = SERVER_ALIASES[last];
    const target = alias ?? key;
    if (!(target in renamed)) renamed[target] = msg;
  }
  return mergeServerErrors<TraditionField>({}, renamed, TRADITION_FIELDS);
}

/** Shown when saving failed for a reason that is not about one field. */
export const TRADITION_SUBMIT_ERRORS = {
  network:
    "We couldn't reach Stitch, so nothing was saved. Everything you wrote is still here: check your connection and save it again.",
  signedOut:
    "You've been signed out, so this wasn't saved. Log in again in another tab, then come back and save it: what you wrote is still here.",
  forbidden: "You can't add traditions to this family any more. What you wrote is still here if you want to copy it.",
  server: "That didn't save just now. Everything you wrote is still here; try saving it again in a moment.",
  busy: "Stitch is busy. Wait a moment, then save it again: what you wrote is still here.",
} as const;

/**
 * The form-level message for a failed save, from the error's HTTP status (0
 * when the server was never reached) and its detail. Field problems (422) and
 * photo problems (413/415) are shown on their fields; this covers the rest,
 * and always says the draft is safe.
 */
export function traditionSubmitErrorMessage(err: { status: number; detail?: string | null } | null | undefined): string {
  const status = err && Number.isFinite(err.status) ? err.status : 0;
  if (status === 0) return TRADITION_SUBMIT_ERRORS.network;
  if (status === 401) return TRADITION_SUBMIT_ERRORS.signedOut;
  if (status === 403) return TRADITION_SUBMIT_ERRORS.forbidden;
  if (status === 429) return TRADITION_SUBMIT_ERRORS.busy;
  if (status >= 500) return TRADITION_SUBMIT_ERRORS.server;
  const detail = (err?.detail ?? "").trim();
  return detail || TRADITION_SUBMIT_ERRORS.server;
}
