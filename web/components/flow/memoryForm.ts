// Pure logic behind the add-a-memory form: which question area it shows, the
// draft it edits, its checks, and the fields it hands to api.submitMemory.
// Erasable TypeScript only.
// The one runtime import (validateYear) is extensionless for the Next build;
// __tests__/memoryForm.test.mjs adds a resolve hook so `node --test` finds it.
import type { Question, SubmitMemoryFields } from "@/lib/types";
import { validateYear, type FieldErrors } from "./validation";

/** Which way the add page is telling: a new moment, or a second telling of one. */
export type QuestionAreaMode = "new" | "second";

/** What the page shows for picking a question. At most one of these sits above the story. */
export interface QuestionArea {
  /** The questions family members asked, as notes above the story. */
  showFamilyQuestions: boolean;
  /** The generic starting points, inside the question field's disclosure. */
  showStarters: boolean;
  /** The optional, typed "What question are you answering?" field below the story. */
  showQuestionField: boolean;
}

/**
 * The one quiet question area of the add page.
 *
 * - A second telling (from `?memoryId=`) is about its moment: no family
 *   questions, no starters and no question field, so nothing can switch the
 *   moment. The moment's own question is read-only context.
 * - A new memory shows the family's open questions when there are any. The
 *   typed question field sits below the story, collapsed, and holds the
 *   generic starters only when nobody asked anything.
 * - Once a question is filled in by a pick (`prefilledQuestion`), the picked
 *   note carries it, so the typed field and starters step aside.
 */
export function questionAreaFor(input: {
  mode: QuestionAreaMode;
  openQuestions: number;
  prefilledQuestion: boolean;
}): QuestionArea {
  if (input.mode === "second") {
    return { showFamilyQuestions: false, showStarters: false, showQuestionField: false };
  }
  const asked = Number.isFinite(input.openQuestions) && input.openQuestions > 0;
  const typed = !input.prefilledQuestion;
  return {
    showFamilyQuestions: asked,
    showStarters: typed && !asked,
    showQuestionField: typed,
  };
}

/** Warm starting points, shown as chips when no question was asked. */
export const SUGGESTED_QUESTIONS: readonly string[] = [
  "How did you two meet?",
  "What was your first house like?",
  "What's a trip you'll never forget?",
  "What did holidays look like when you were small?",
];

/** Longest story, in characters (code points). */
export const TEXT_MAX = 10000;
/** Longest question a person can type, in characters (code points). */
export const QUESTION_MAX = 300;
/**
 * Longest question the API takes, in characters (code points). Mirrors
 * api/routes/memories.py MAX_QUESTION_CHARS. A question the person cannot edit
 * (a family member's, or a moment's) is clamped to this before sending.
 */
export const SERVER_QUESTION_MAX = 1000;
/** Earliest year a memory can have. Mirrors api/routes/memories.py MIN_YEAR. */
export const MEMORY_YEAR_MIN = 1800;

/** What the form edits. `year` stays a string until it is sent. */
export interface MemoryDraft {
  question: string;
  text: string;
  year: string;
  participantIds: string[];
  /** Set when this is a second telling of an existing moment. */
  memoryId: string | null;
}

export type MemoryField = "question" | "text" | "year" | "participantIds" | "photos";

/** Every field, in form order, for mergeServerErrors. */
export const MEMORY_FIELDS: readonly MemoryField[] = [
  "question",
  "text",
  "year",
  "photos",
  "participantIds",
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

/** A fresh draft. The author is always a participant, listed first, once. */
export function emptyDraft(selfPersonId: string, initial?: Partial<MemoryDraft>): MemoryDraft {
  const others = (initial?.participantIds ?? []).filter((id) => id !== selfPersonId);
  return {
    question: initial?.question ?? "",
    text: initial?.text ?? "",
    year: initial?.year ?? "",
    participantIds: uniqueIds([selfPersonId, ...others]),
    memoryId: initial?.memoryId ?? null,
  };
}

/**
 * Draft fields for answering a pending question. With the moment it belongs
 * to (a second telling), the year and people come from that moment.
 */
export function draftFromQuestion(
  q: Pick<Question, "text" | "memoryId">,
  moment?: { year: number; participantIds?: string[] } | null,
): Partial<MemoryDraft> {
  const out: Partial<MemoryDraft> = { question: q.text, memoryId: q.memoryId ?? null };
  if (moment) {
    out.year = String(moment.year);
    if (moment.participantIds) out.participantIds = uniqueIds(moment.participantIds);
  }
  return out;
}

/** Which draft fields the person can change on screen. Both default to true. */
export interface DraftEditable {
  /** False when the question is someone else's (a picked note, a moment's): it is not capped here. */
  questionEditable?: boolean;
  /** False for a second telling: the year is the moment's, shown read-only. */
  yearEditable?: boolean;
}

/**
 * Friendly messages for what is wrong before sending. Participants are only
 * checked for a new moment: a second telling (memoryId set) keeps the moment's.
 * A field the person cannot edit never blocks sending with a message they
 * could not act on: an uneditable question is not capped (toSubmitFields
 * clamps it to what the API takes), and an uneditable year is only checked
 * for being a whole number, not range-checked.
 */
export function validateDraft(
  d: MemoryDraft,
  selfPersonId: string,
  opts: DraftEditable = {},
): FieldErrors<MemoryField> {
  const questionEditable = opts.questionEditable ?? true;
  const yearEditable = opts.yearEditable ?? true;
  const errors: FieldErrors<MemoryField> = {};

  const question = d.question.trim();
  if (questionEditable && charCount(question) > QUESTION_MAX) {
    errors.question = `Keep the question to ${QUESTION_MAX} characters or fewer.`;
  }

  const text = d.text.trim();
  if (!text) errors.text = "Tell the story in your own words.";
  else if (charCount(text) > TEXT_MAX) {
    errors.text = `Keep your story to ${countFormat.format(TEXT_MAX)} characters or fewer.`;
  }

  const year = yearEditable
    ? validateYear(d.year, { required: true, min: MEMORY_YEAR_MIN })
    : validateYear(d.year, { required: true, min: -Infinity, max: Infinity });
  if (year) errors.year = year;

  if (!d.memoryId && !d.participantIds.includes(selfPersonId)) {
    errors.participantIds = "You were there too: keep yourself in the list.";
  }

  return errors;
}

/** At most SERVER_QUESTION_MAX code points: a longer question keeps its start and ends in "…". */
function clampQuestion(q: string): string {
  const chars = Array.from(q);
  if (chars.length <= SERVER_QUESTION_MAX) return q;
  return `${chars.slice(0, SERVER_QUESTION_MAX - 1).join("")}…`;
}

/** The multipart fields for api.submitMemory. Photos travel separately. */
export function toSubmitFields(d: MemoryDraft): SubmitMemoryFields {
  const fields: SubmitMemoryFields = { text: d.text.trim(), year: Number(d.year.trim()) };
  const question = d.question.trim();
  if (question) fields.question = clampQuestion(question);
  const memoryId = d.memoryId?.trim();
  // A second telling joins a moment that already has its people.
  if (memoryId) fields.memoryId = memoryId;
  else fields.participantIds = uniqueIds(d.participantIds);
  return fields;
}

// ---------------------------------------------------------------------------
// When sending fails

/** Shown when sending failed for a reason that is not about one field. */
export const SUBMIT_ERRORS = {
  network:
    "We couldn't reach Stitch, so nothing was sent. Your story is still here: check your connection and send it again.",
  signedOut:
    "You've been signed out, so this wasn't sent. Log in again in another tab, then come back and send it: your story is still here.",
  forbidden: "You can't add memories to this family any more. Your story is still here if you want to copy it.",
  server:
    "The studio couldn't take your story just now. Everything you wrote is still here; try sending it again in a moment.",
  busy: "The studio is busy. Wait a moment, then send it again: your story is still here.",
  momentGone:
    "That moment isn't in the family any more, so your telling couldn't join it. Your story is still here.",
} as const;

/**
 * The form-level message for a failed send, from the error's HTTP status
 * (0 when the server was never reached) and its detail. Field problems (422)
 * and photo problems (413/415) are shown on their fields; this covers the
 * rest, and always says the draft is safe.
 */
export function submitErrorMessage(
  err: { status: number; detail?: string | null } | null | undefined,
  secondTelling = false,
): string {
  const status = err && Number.isFinite(err.status) ? err.status : 0;
  if (status === 0) return SUBMIT_ERRORS.network;
  if (status === 401) return SUBMIT_ERRORS.signedOut;
  if (status === 403) return SUBMIT_ERRORS.forbidden;
  if (status === 404 && secondTelling) return SUBMIT_ERRORS.momentGone;
  if (status === 429) return SUBMIT_ERRORS.busy;
  if (status >= 500) return SUBMIT_ERRORS.server;
  const detail = (err?.detail ?? "").trim();
  return detail || SUBMIT_ERRORS.server;
}

// ---------------------------------------------------------------------------
// Finding people in a big family

/** A family with more people than this gets a "Find someone" field over the picker. */
export const PICKER_SEARCH_FROM = 10;

/** Lowercased, accents and extra spaces removed, for forgiving name matching. */
function foldName(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * The people whose name contains `query`, ignoring case, accents and extra
 * spaces, keeping their order.
 * People in `keepIds` (the picked ones) always stay, so a search never hides
 * who is already in. A blank query keeps everyone.
 */
export function filterPeople<P extends { id: string; name: string }>(
  people: readonly P[],
  query: string,
  keepIds: readonly string[] = [],
): P[] {
  const q = foldName(query);
  if (!q) return [...people];
  return people.filter((p) => {
    if (keepIds.includes(p.id)) return true;
    return foldName(p.name).includes(q);
  });
}

/**
 * What a key press in the picker's "Find someone" field does. The field sits
 * inside the memory form, so Enter must never fall through to the form (that
 * would send the memory, or fire its validation, mid-search):
 * - "pick": Enter with exactly one unpicked match picks that person
 *   (`pickId`) and clears the search;
 * - "stay": any other Enter is swallowed and nothing changes;
 * - "clear": Escape with something typed empties the search;
 * - null: every other key behaves as normal.
 * Callers call preventDefault for any non-null action.
 */
export function pickerSearchKey<P extends { id: string; name: string }>(
  key: string,
  query: string,
  people: readonly P[],
  pickedIds: readonly string[] = [],
): { action: "pick"; pickId: string } | { action: "stay" | "clear" } | null {
  if (key === "Escape") return query ? { action: "clear" } : null;
  if (key !== "Enter") return null;
  if (!foldName(query)) return { action: "stay" };
  const matches = filterPeople(people, query);
  if (matches.length === 1 && !pickedIds.includes(matches[0].id)) {
    return { action: "pick", pickId: matches[0].id };
  }
  return { action: "stay" };
}
