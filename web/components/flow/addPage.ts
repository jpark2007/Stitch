// Pure helpers behind the add-a-memory page (app/add/): reading its query,
// the URL a run lives at, the telling slip kept per run so a reload can show
// it again, the story as it was sent (so a failed run can be told again), and
// the moment a second telling joins.
//
// No runtime imports and only erasable TypeScript, so `node --test` loads this
// file directly. Tests: components/flow/__tests__/addPage.test.mjs.
import type { RunSource } from "@/components/agent/useAgentRun";
import type { RunEventMessage } from "@/components/agent/runState";
import type {
  FamilySummary,
  MemoryResponse,
  MomentSummary,
  Question,
  SubmitMemoryFields,
  ThreadsResponse,
} from "../../lib/types";
import type { MemoryRunTelling } from "./MemoryRun";
import type { MemoryDraft } from "./memoryForm";

/** What /add reads from its query string. Missing or empty values are null. */
export interface AddQuery {
  /** `?family=`: the family to add to (else the remembered one). */
  familyId: string | null;
  /** `?memoryId=`: add a second telling to this moment. */
  memoryId: string | null;
  /** `?run=`: a run already under way; show it instead of the form. */
  runId: string | null;
  /** `?retry=`: a run that failed; bring back the form with what it was sent. */
  retryOf: string | null;
}

/** `/add`'s query, from location.search, a URLSearchParams or nothing. */
export function readAddQuery(search: string | URLSearchParams | null): AddQuery {
  let params: URLSearchParams;
  if (search === null || search === undefined) params = new URLSearchParams();
  else if (typeof search === "string") params = new URLSearchParams(search);
  else params = search;
  const read = (name: string): string | null => {
    const v = params.get(name);
    return v ? v : null;
  };
  return { familyId: read("family"), memoryId: read("memoryId"), runId: read("run"), retryOf: read("retry") };
}

/** Where a run of family `familyId` lives: reloading it reattaches to the run. */
export function runHref(q: { familyId: string; runId: string }): string {
  return `/add?family=${encodeURIComponent(q.familyId)}&run=${encodeURIComponent(q.runId)}`;
}

/** The form again, filled in with what run `runId` was sent (after it failed). */
export function retryHref(q: { familyId: string; runId: string }): string {
  return `/add?family=${encodeURIComponent(q.familyId)}&retry=${encodeURIComponent(q.runId)}`;
}

// ---------------------------------------------------------------------------
// Which family the memory goes into

/** The family /add is adding to, the others it could add to, and whether to say so. */
export interface FamilyChoice {
  /** The family the memory goes into; null only for someone with no family. */
  current: FamilySummary | null;
  /** Every other family, in the order the session lists them. */
  others: FamilySummary[];
  /** True only for someone in two or more families: one family needs no naming. */
  show: boolean;
}

/**
 * The family choice on /add. `current` falls back exactly as pickFamily does
 * (the matching family, else the first one), so the page and this agree.
 */
export function familyChoice(
  families: readonly FamilySummary[],
  currentId: string | null | undefined,
): FamilyChoice {
  const current = (currentId ? families.find((f) => f.id === currentId) : undefined) ?? families[0] ?? null;
  const others = current ? families.filter((f) => f.id !== current.id) : [];
  return { current, others, show: families.length >= 2 };
}

/** The slip MemoryRun shows above the feed, from what was just sent. */
export function tellingFromSubmit(
  fields: SubmitMemoryFields,
  photoCount: number,
  secondTellingOf?: string | null,
): MemoryRunTelling {
  const question = typeof fields.question === "string" ? fields.question.trim() : "";
  const title = typeof secondTellingOf === "string" ? secondTellingOf.trim() : "";
  return {
    question: question || null,
    text: fields.text,
    year: fields.year,
    photoCount: Number.isFinite(photoCount) && photoCount > 0 ? Math.floor(photoCount) : 0,
    secondTellingOf: title || null,
  };
}

// ---------------------------------------------------------------------------
// The telling slip, kept in sessionStorage per run. Storage can be missing or
// throw (private mode, blocked site data); these helpers never throw.

/** sessionStorage key prefix; the run id follows. */
export const TELLING_KEY_PREFIX = "stitch.telling.";

export function saveTelling(
  storage: Pick<Storage, "setItem"> | null,
  runId: string,
  t: MemoryRunTelling,
): void {
  if (!storage || !runId) return;
  try {
    storage.setItem(TELLING_KEY_PREFIX + runId, JSON.stringify(t));
  } catch {
    // Not kept: a reload shows the run without its slip.
  }
}

function isOptionalString(v: unknown): boolean {
  return v === undefined || v === null || typeof v === "string";
}

/** The slip saved for `runId`, or null when there is none or it is unreadable. */
export function loadTelling(
  storage: Pick<Storage, "getItem"> | null,
  runId: string,
): MemoryRunTelling | null {
  if (!storage || !runId) return null;
  let raw: string | null;
  try {
    raw = storage.getItem(TELLING_KEY_PREFIX + runId);
  } catch {
    return null;
  }
  if (typeof raw !== "string" || !raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const o = parsed as Record<string, unknown>;
  if (typeof o.text !== "string") return null;
  if (typeof o.year !== "number" || !Number.isFinite(o.year)) return null;
  if (typeof o.photoCount !== "number" || !Number.isFinite(o.photoCount) || o.photoCount < 0) return null;
  if (!isOptionalString(o.question) || !isOptionalString(o.secondTellingOf)) return null;
  return {
    question: (o.question as string | null | undefined) ?? null,
    text: o.text,
    year: o.year,
    photoCount: o.photoCount,
    secondTellingOf: (o.secondTellingOf as string | null | undefined) ?? null,
  };
}

// ---------------------------------------------------------------------------
// The story as it was sent, kept in sessionStorage per run, so a run that
// fails can bring the form back filled in ("Tell it again"). Photos are files
// and cannot be stored: only how many there were. These helpers never throw.

/** sessionStorage key prefix; the run id follows. */
export const DRAFT_KEY_PREFIX = "stitch.draft.";

/** What a run was sent, enough to fill the form in again. */
export interface SentDraft {
  text: string;
  year: number;
  question: string | null;
  /** Set for a second telling of this moment. */
  memoryId: string | null;
  participantIds: string[];
  /** How many photos went with it (the files themselves are not kept). */
  photoCount: number;
}

function uniqueStrings(ids: readonly unknown[]): string[] {
  const out: string[] = [];
  for (const id of ids) {
    if (typeof id === "string" && id && !out.includes(id)) out.push(id);
  }
  return out;
}

/** The SentDraft for what the form just sent. */
export function sentDraftFromSubmit(fields: SubmitMemoryFields, photoCount: number): SentDraft {
  const question = typeof fields.question === "string" ? fields.question.trim() : "";
  const memoryId = typeof fields.memoryId === "string" ? fields.memoryId.trim() : "";
  return {
    text: fields.text,
    year: fields.year,
    question: question || null,
    memoryId: memoryId || null,
    participantIds: uniqueStrings(fields.participantIds ?? []),
    photoCount: Number.isFinite(photoCount) && photoCount > 0 ? Math.floor(photoCount) : 0,
  };
}

export function saveSentDraft(storage: Pick<Storage, "setItem"> | null, runId: string, d: SentDraft): void {
  if (!storage || !runId) return;
  try {
    storage.setItem(DRAFT_KEY_PREFIX + runId, JSON.stringify(d));
  } catch {
    // Not kept: "Tell it again" falls back to a blank form.
  }
}

/** The draft saved for `runId`, or null when there is none or it is unreadable. */
export function loadSentDraft(storage: Pick<Storage, "getItem"> | null, runId: string): SentDraft | null {
  if (!storage || !runId) return null;
  let raw: string | null;
  try {
    raw = storage.getItem(DRAFT_KEY_PREFIX + runId);
  } catch {
    return null;
  }
  if (typeof raw !== "string" || !raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const o = parsed as Record<string, unknown>;
  if (typeof o.text !== "string") return null;
  if (typeof o.year !== "number" || !Number.isFinite(o.year)) return null;
  if (typeof o.photoCount !== "number" || !Number.isFinite(o.photoCount) || o.photoCount < 0) return null;
  if (!isOptionalString(o.question) || !isOptionalString(o.memoryId)) return null;
  if (!Array.isArray(o.participantIds) || !o.participantIds.every((id) => typeof id === "string")) return null;
  return {
    text: o.text,
    year: o.year,
    question: (o.question as string | null | undefined) || null,
    memoryId: (o.memoryId as string | null | undefined) || null,
    participantIds: uniqueStrings(o.participantIds),
    photoCount: Math.floor(o.photoCount),
  };
}

/** The form's starting values for a SentDraft. */
export function draftFromSent(d: SentDraft): Partial<MemoryDraft> {
  return {
    text: d.text,
    year: String(d.year),
    question: d.question ?? "",
    memoryId: d.memoryId,
    participantIds: [...d.participantIds],
  };
}

/**
 * The note above a form brought back after a failed run. Photos cannot be
 * kept across a reload: when fewer come back than were sent (`photosBack`
 * null means none), it asks for them again.
 */
export function restoreNotice(photoCount: number, photosBack: number | null): string {
  const lead = "The studio couldn't finish your last one, so here it is again. Change anything you like and send it.";
  const sent = Number.isFinite(photoCount) && photoCount > 0 ? Math.floor(photoCount) : 0;
  if (sent === 0 || (photosBack !== null && photosBack >= sent)) return lead;
  return sent === 1
    ? `${lead} Your photo didn't come along; add it again.`
    : `${lead} Your photos didn't come along; add them again.`;
}

// ---------------------------------------------------------------------------
// The story typed so far, carried across a switch to another family. A switch
// is a page change, so the form would start blank; this keeps the words and
// the year (not the people: they belong to the family left behind). Kept in
// sessionStorage for one read. These helpers never throw.

/** sessionStorage key for the story carried into another family's form. */
export const CARRY_KEY = "stitch.addCarry";

/** What is carried: the story and year, for the form of family `familyId`. */
export interface CarriedStory {
  familyId: string;
  text: string;
  year: string;
}

/**
 * Keeps what was typed for the form of family `familyId`. Nothing typed
 * clears anything kept before, so an old story never turns up later.
 */
export function saveCarry(
  storage: Pick<Storage, "setItem" | "removeItem"> | null,
  c: CarriedStory,
): void {
  if (!storage) return;
  try {
    if (!c.familyId || (!c.text.trim() && !c.year.trim())) storage.removeItem(CARRY_KEY);
    else storage.setItem(CARRY_KEY, JSON.stringify({ familyId: c.familyId, text: c.text, year: c.year }));
  } catch {
    // Not kept: the other family's form starts blank.
  }
}

/**
 * The story carried into family `familyId`'s form, or null when there is
 * none, it was for another family, or it is unreadable. Reading leaves it in
 * place (React may run a state initializer twice); clearCarry drops it once
 * the form has it, so a reload or a later visit starts blank.
 */
export function readCarry(
  storage: Pick<Storage, "getItem"> | null,
  familyId: string,
): { text: string; year: string } | null {
  if (!storage || !familyId) return null;
  let raw: string | null;
  try {
    raw = storage.getItem(CARRY_KEY);
  } catch {
    return null;
  }
  if (typeof raw !== "string" || !raw) return null;
  let o: unknown;
  try {
    o = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof o !== "object" || o === null || Array.isArray(o)) return null;
  const c = o as Record<string, unknown>;
  if (c.familyId !== familyId || typeof c.text !== "string" || typeof c.year !== "string") return null;
  const year = /^\d{1,4}$/.test(c.year.trim()) ? c.year.trim() : "";
  if (!c.text.trim() && !year) return null;
  return { text: c.text, year };
}

/** Drops the carried story. Never throws. */
export function clearCarry(storage: Pick<Storage, "removeItem"> | null): void {
  try {
    storage?.removeItem(CARRY_KEY);
  } catch {
    // Blocked storage: nothing was kept either.
  }
}

// ---------------------------------------------------------------------------
// Second tellings

/** The parts of a moment a second telling needs: its title, year, people and who told it. */
export interface MomentInfo {
  title: string;
  year: number;
  participantIds: string[];
  /** Person ids of everyone who has told this moment, in order, once each. */
  tellerIds: string[];
}

/** The moment a second telling joins, from GET /memories/{id}. */
export function momentFromMemory(m: MemoryResponse): MomentInfo {
  return {
    title: m.memory.title,
    year: m.memory.year,
    participantIds: [...(m.memory.participantIds ?? [])],
    tellerIds: uniqueStrings((m.memory.tellings ?? []).map((t) => t?.authorId)),
  };
}

/** The same, from a moment in GET /threads (no scene, so nothing that can 404). */
export function momentFromSummary(s: MomentSummary): MomentInfo {
  return {
    title: s.title,
    year: s.year,
    participantIds: [...(s.participantIds ?? [])],
    tellerIds: uniqueStrings(s.tellingAuthorIds ?? []),
  };
}

/**
 * Every moment of the family by id, from GET /threads, so /add can open a
 * second telling (from `?memoryId=` or a picked question) without asking for
 * the whole scene, and can tell a moment that is not there from a failed
 * load. Null when the threads could not be read: the page then asks for the
 * one moment instead.
 */
export function momentsFromThreads(t: ThreadsResponse | null | undefined): Map<string, MomentInfo> | null {
  if (!t || !Array.isArray(t.moments)) return null;
  const out = new Map<string, MomentInfo>();
  for (const s of t.moments) {
    if (s && typeof s.id === "string" && s.id && !out.has(s.id)) out.set(s.id, momentFromSummary(s));
  }
  return out;
}

/** True when `personId` has already told this moment (telling it again replaces theirs). */
export function hasTold(moment: Pick<MomentInfo, "tellerIds"> | null | undefined, personId: string): boolean {
  return Boolean(moment && personId && moment.tellerIds.includes(personId));
}

/** The open question asking the caller about moment `memoryId`, if there is one. */
export function questionForMemory(
  questions: readonly Question[],
  memoryId: string | null,
): Question | null {
  if (!memoryId) return null;
  return questions.find((q) => q.memoryId === memoryId) ?? null;
}

// ---------------------------------------------------------------------------
// Reattaching to a run

/** Shown when the run a `?run=` link names is gone (finished long ago, or never ours). */
export const RUN_GONE_MESSAGE =
  "We couldn't find this run any more. It may have finished a while ago, or the studio restarted.";

function statusOf(e: unknown): number | null {
  if (typeof e !== "object" || e === null) return null;
  const status = (e as { status?: unknown }).status;
  return typeof status === "number" ? status : null;
}

/**
 * `source` with one change: a stream that is refused with 404 (no such run in
 * this family) ends the run with an `error` event saying so, instead of
 * throwing. Without this, a stale `?run=` link would retry for half a minute
 * before showing anything. Other failures still throw, so the hook reconnects.
 *
 * `onGone`, when given, is told first, so the page can replace the feed (whose
 * Retry could only meet the same 404) with its own way forward.
 */
export function withRunGone(
  source: RunSource,
  message: string = RUN_GONE_MESSAGE,
  onGone?: () => void,
): RunSource {
  return {
    async *stream(signal: AbortSignal): AsyncGenerator<RunEventMessage> {
      try {
        yield* source.stream(signal);
      } catch (e) {
        if (statusOf(e) !== 404) throw e;
        onGone?.();
        yield { event: "error", data: { message } };
      }
    },
    answer: (body) => source.answer(body),
  };
}

// ---------------------------------------------------------------------------
// Carrying a draft into a remounted form

/** What the add page carries from one form into the next (photos travel apart). */
export interface CarriedDraft {
  text: string;
  year: string;
  /** People picked for a new memory; null when none were chosen (a second telling). */
  participantIds: string[] | null;
}

/**
 * The starting values for a form that replaces another (a picked question, a
 * released one, another family's form), with what was typed carried in: the
 * story always; the year and people only into a new memory that has none of
 * its own (a second telling keeps its moment's). Nothing carried leaves
 * `initial` as it was.
 */
export function carryInto(
  initial: Partial<MemoryDraft> | undefined,
  carried: CarriedDraft,
  secondTelling: boolean,
): Partial<MemoryDraft> | undefined {
  const out: Partial<MemoryDraft> = {};
  if (carried.text) out.text = carried.text;
  if (!secondTelling) {
    if (carried.year && !initial?.year) out.year = carried.year;
    if (carried.participantIds && carried.participantIds.length > 0 && !initial?.participantIds?.length) {
      out.participantIds = uniqueStrings(carried.participantIds);
    }
  }
  return Object.keys(out).length ? { ...initial, ...out } : initial;
}
