// Pure pieces of the "Make a film" form: which picked files to keep, the line
// that makes the 8-to-40 rule obvious, the checks before sending, the upload
// queue's bookkeeping, and the body for createFilm.
//
// Erasable TypeScript; its one runtime import ("../flow/validation") is
// itself import-free. Tests: components/film/__tests__/filmForm.test.mjs.
import type { CreateFilmRequest } from "@/lib/types";

import { validateYear, type FieldErrors } from "../flow/validation";

/** Mirrors api/schemas.py MIN_FILM_PHOTOS / MAX_FILM_PHOTOS (and lib/api.ts; a test keeps them equal). */
export const MIN_FILM_PHOTOS = 8;
export const MAX_FILM_PHOTOS = 40;
/** api/schemas.py MAX_FILM_TITLE_CHARS and MAX_FILM_TEXT_CHARS. */
export const FILM_TITLE_MAX = 200;
export const FILM_TEXT_MAX = 20_000;
/** api/schemas.py MIN_FILM_YEAR. */
export const FILM_YEAR_MIN = 1800;
/** Largest single photo, in bytes: the media upload's limit (10 MB). */
export const FILM_PHOTO_MAX_BYTES = 10 * 1024 * 1024;
/** At most this many photos upload at once. */
export const UPLOADS_IN_FLIGHT = 3;

/** The photo kinds a film takes. An iPhone hands over JPEGs for this list. */
export const FILM_PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
/** For the file input's `accept`. */
export const FILM_ACCEPT = FILM_PHOTO_TYPES.join(",");

const TYPES_BY_EXTENSION: Record<string, string> = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".webp": "image/webp",
};

/** The fields of the form, as FieldErrors keys. */
export const FILM_FIELDS = ["photos", "text", "year", "title"] as const;
export type FilmField = (typeof FILM_FIELDS)[number];

interface FileLike {
  name: string;
  size: number;
  type: string;
}

function typeOf(file: FileLike): string | null {
  const type = file.type.trim().toLowerCase();
  if (type) return type;
  const name = file.name.toLowerCase();
  const dot = name.lastIndexOf(".");
  return (dot >= 0 && TYPES_BY_EXTENSION[name.slice(dot)]) || null;
}

function quoted(name: string): string {
  return `“${name || "That file"}”`;
}

function namesList(names: readonly string[]): string {
  const shown = names.slice(0, 3).map(quoted);
  const rest = names.length - shown.length;
  if (rest > 0) return `${shown.join(", ")} and ${rest} more`;
  if (shown.length === 1) return shown[0];
  return `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
}

/** A stable identity for a picked file (same name and size counts as the same photo). */
export function fileIdentity(file: FileLike): string {
  return `${file.name}\u0000${file.size}`;
}

/**
 * Which newly picked files to keep, given those already picked: the indices
 * of `incoming` to add, in order, and a friendly line for each kind of file
 * left out (already added, not a JPEG/PNG/WebP, empty, over 10 MB, over the
 * 40-photo cap). Several files left out for the same reason share one line.
 */
export function acceptFilmPhotos(
  current: readonly FileLike[],
  incoming: readonly FileLike[],
  max: number = MAX_FILM_PHOTOS,
): { accepted: number[]; errors: string[] } {
  const accepted: number[] = [];
  const seen = new Set(current.map(fileIdentity));
  let room = Math.max(0, max - current.length);
  const dupes: string[] = [];
  const wrongType: string[] = [];
  const empty: string[] = [];
  const tooBig: string[] = [];
  const overCap: string[] = [];

  incoming.forEach((file, i) => {
    const key = fileIdentity(file);
    const type = typeOf(file);
    if (seen.has(key)) dupes.push(file.name);
    else if (!type || !(FILM_PHOTO_TYPES as readonly string[]).includes(type)) wrongType.push(file.name);
    else if (file.size <= 0) empty.push(file.name);
    else if (file.size > FILM_PHOTO_MAX_BYTES) tooBig.push(file.name);
    else if (room <= 0) overCap.push(file.name);
    else {
      seen.add(key);
      room -= 1;
      accepted.push(i);
    }
  });

  const errors: string[] = [];
  const is = (n: number) => (n === 1 ? "is" : "are");
  if (dupes.length) errors.push(`${namesList(dupes)} ${is(dupes.length)} already in.`);
  if (wrongType.length) {
    errors.push(`${namesList(wrongType)} ${wrongType.length === 1 ? "isn’t a photo" : "aren’t photos"} we can use. Try JPEG, PNG or WebP.`);
  }
  if (empty.length) errors.push(`${namesList(empty)} ${empty.length === 1 ? "looks" : "look"} empty. Try picking ${empty.length === 1 ? "it" : "them"} again.`);
  if (tooBig.length) errors.push(`${namesList(tooBig)} ${is(tooBig.length)} over 10 MB.`);
  if (overCap.length) {
    errors.push(`${namesList(overCap)} ${overCap.length === 1 ? "wasn’t" : "weren’t"} added: a film can use up to ${max} photos.`);
  }
  return { accepted, errors };
}

/** The line under the photos that makes the 8-to-40 rule obvious. `ready` once there are enough. */
export function photoCountLine(count: number): { text: string; ready: boolean } {
  const n = Math.max(0, Math.floor(count));
  if (n === 0) return { text: `Pick ${MIN_FILM_PHOTOS} to ${MAX_FILM_PHOTOS} photos from one day or trip.`, ready: false };
  if (n < MIN_FILM_PHOTOS) {
    const more = MIN_FILM_PHOTOS - n;
    return { text: `${n} ${n === 1 ? "photo" : "photos"}. Add at least ${more} more.`, ready: false };
  }
  if (n >= MAX_FILM_PHOTOS) return { text: `${n} photos, the most one film can use.`, ready: true };
  return { text: `${n} photos. Stitch picks the best of them.`, ready: true };
}

/** What was typed, as strings (the year as typed). */
export interface FilmDraft {
  text: string;
  year: string;
  title: string;
}

function charCount(s: string): number {
  return Array.from(s).length;
}

/** The checks before sending: enough photos, a story, a year, a title that fits. */
export function validateFilmDraft(draft: FilmDraft, photoCount: number): FieldErrors<FilmField> {
  const errors: FieldErrors<FilmField> = {};
  if (photoCount < MIN_FILM_PHOTOS) {
    const more = MIN_FILM_PHOTOS - photoCount;
    errors.photos =
      photoCount === 0
        ? `Add ${MIN_FILM_PHOTOS} to ${MAX_FILM_PHOTOS} photos.`
        : `A film needs at least ${MIN_FILM_PHOTOS} photos. Add ${more} more.`;
  } else if (photoCount > MAX_FILM_PHOTOS) {
    errors.photos = `A film can use up to ${MAX_FILM_PHOTOS} photos. Remove ${photoCount - MAX_FILM_PHOTOS}.`;
  }
  const text = draft.text.trim();
  if (!text) errors.text = "Tell what happened. A few sentences is plenty.";
  else if (charCount(text) > FILM_TEXT_MAX) errors.text = `Keep it under ${FILM_TEXT_MAX.toLocaleString("en-US")} characters.`;
  const year = validateYear(draft.year, { min: FILM_YEAR_MIN, required: true });
  if (year) errors.year = year;
  if (charCount(draft.title.trim()) > FILM_TITLE_MAX) errors.title = `Keep the title to ${FILM_TITLE_MAX} characters.`;
  return errors;
}

/** The createFilm body for a checked draft and the uploaded photo ids, in the order picked. */
export function filmRequest(draft: FilmDraft, photoIds: readonly string[]): CreateFilmRequest {
  const title = draft.title.trim();
  return { title: title || null, text: draft.text.trim(), year: Number(draft.year.trim()), photoIds: [...photoIds] };
}

/**
 * A 422's field messages on the form's fields: `photoIds` is the photos,
 * anything else the form has is itself; the rest is one form-level line.
 */
export function filmServerErrors(server: Record<string, string>): { fields: FieldErrors<FilmField>; rest: string | null } {
  const fields: FieldErrors<FilmField> = {};
  const rest: string[] = [];
  for (const [key, msg] of Object.entries(server)) {
    if (typeof msg !== "string" || !msg.trim()) continue;
    const field = key === "photoIds" ? "photos" : (FILM_FIELDS as readonly string[]).includes(key) ? (key as FilmField) : null;
    if (field) fields[field] = msg;
    else if (!rest.includes(msg)) rest.push(msg);
  }
  return { fields, rest: rest.length ? rest.join(" ") : null };
}

// ---------------------------------------------------------------------------
// The upload queue

export type UploadStatus = "waiting" | "sending" | "sent" | "failed";

export interface UploadState {
  status: UploadStatus;
  /** The bare media id once sent. */
  mediaId: string | null;
}

/** Counts for the progress line. */
export interface UploadTally {
  total: number;
  sent: number;
  sending: number;
  failed: number;
  waiting: number;
}

export function tallyUploads(items: readonly UploadState[]): UploadTally {
  const t: UploadTally = { total: items.length, sent: 0, sending: 0, failed: 0, waiting: 0 };
  for (const i of items) {
    if (i.status === "sent" && i.mediaId) t.sent += 1;
    else if (i.status === "sending") t.sending += 1;
    else if (i.status === "failed") t.failed += 1;
    else t.waiting += 1;
  }
  return t;
}

/** The honest line beside the uploads while they run: counts only, no made-up percentages. */
export function uploadLine(t: UploadTally): string {
  if (t.total === 0) return "";
  if (t.failed > 0 && t.sending === 0) {
    return t.failed === 1
      ? "One photo didn’t send. Try it again, or remove it."
      : `${t.failed} photos didn’t send. Try them again, or remove them.`;
  }
  if (t.sent === t.total) return t.total === 1 ? "Photo sent." : `All ${t.total} photos sent.`;
  return `Sending photos: ${t.sent} of ${t.total} sent`;
}

/** The media ids in order when every photo is sent; null while any is not. */
export function sentIds(items: readonly UploadState[]): string[] | null {
  const ids: string[] = [];
  for (const i of items) {
    if (i.status !== "sent" || !i.mediaId) return null;
    ids.push(i.mediaId);
  }
  return ids;
}

/**
 * Runs `upload` over `keys` with at most `limit` in flight, each worker
 * taking the next key when its last one settles. Resolves when all have
 * settled (failures are the caller's to record in `upload`).
 */
export async function runQueue(
  keys: readonly string[],
  upload: (key: string) => Promise<void>,
  limit: number = UPLOADS_IN_FLIGHT,
): Promise<void> {
  let next = 0;
  const worker = async () => {
    while (next < keys.length) {
      const key = keys[next++];
      try {
        await upload(key);
      } catch {
        // The caller records its own failures; one never stops the others.
      }
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(limit, keys.length)) }, worker));
}
