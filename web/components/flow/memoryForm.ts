// Pure logic behind the add-a-memory form: the draft it edits, its checks,
// and the fields it hands to api.submitMemory. Erasable TypeScript only.
// The one runtime import (validateYear) is extensionless for the Next build;
// __tests__/memoryForm.test.mjs adds a resolve hook so `node --test` finds it.
import type { Question, SubmitMemoryFields } from "@/lib/types";
import { validateYear, type FieldErrors } from "./validation";

/** Warm starting points, shown as chips when no question was asked. */
export const SUGGESTED_QUESTIONS: readonly string[] = [
  "How did you two meet?",
  "What was your first house like?",
  "What's a trip you'll never forget?",
  "What did holidays look like when you were small?",
];

/** Longest story, in characters (code points). */
export const TEXT_MAX = 10000;
/** Longest question, in characters (code points). */
export const QUESTION_MAX = 300;

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

/**
 * Friendly messages for what is wrong before sending. Participants are only
 * checked for a new moment: a second telling (memoryId set) keeps the moment's.
 */
export function validateDraft(d: MemoryDraft, selfPersonId: string): FieldErrors<MemoryField> {
  const errors: FieldErrors<MemoryField> = {};

  const question = d.question.trim();
  if (charCount(question) > QUESTION_MAX) {
    errors.question = `Keep the question to ${QUESTION_MAX} characters or fewer.`;
  }

  const text = d.text.trim();
  if (!text) errors.text = "Tell the story in your own words.";
  else if (charCount(text) > TEXT_MAX) {
    errors.text = `Keep your story to ${countFormat.format(TEXT_MAX)} characters or fewer.`;
  }

  const year = validateYear(d.year, { required: true });
  if (year) errors.year = year;

  if (!d.memoryId && !d.participantIds.includes(selfPersonId)) {
    errors.participantIds = "You were there too: keep yourself in the list.";
  }

  return errors;
}

/** The multipart fields for api.submitMemory. Photos travel separately. */
export function toSubmitFields(d: MemoryDraft): SubmitMemoryFields {
  const fields: SubmitMemoryFields = { text: d.text.trim(), year: Number(d.year.trim()) };
  const question = d.question.trim();
  if (question) fields.question = question;
  const memoryId = d.memoryId?.trim();
  // A second telling joins a moment that already has its people.
  if (memoryId) fields.memoryId = memoryId;
  else fields.participantIds = uniqueIds(d.participantIds);
  return fields;
}
