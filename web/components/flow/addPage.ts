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

/**
 * `?added=<personId>`: someone just added on the add-relative page, to tick
 * under "Who was there?". Null when missing or empty.
 */
export function addedFromQuery(search: string | URLSearchParams | null): string | null {
  if (search === null || search === undefined) return null;
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const v = params.get("added");
  return v ? v : null;
}

/**
 * This /add URL without `?added=`, every other param kept as it was
 * (family, memoryId, run, retry), so a reload after the person was ticked
 * (and maybe unticked) does not tick them again.
 */
export function withoutAdded(search: string | URLSearchParams | null): string {
  const params = new URLSearchParams(search === null || search === undefined ? "" : search);
  params.delete("added");
  const q = params.toString();
  return q ? `/add?${q}` : "/add";
}

/**
 * `?questionId=<id>`: the family question picked on /add, kept in the URL so
 * a reload opens the same question (and, for one about a moment, the same
 * second telling). Null when missing or empty.
 */
export function questionIdFromQuery(search: string | URLSearchParams | null): string | null {
  if (search === null || search === undefined) return null;
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const v = params.get("questionId");
  return v ? v : null;
}

/**
 * This /add URL with `?questionId=` set to `questionId` (dropped for null or
 * ""), every other param kept as it was (family, memoryId, run, retry...).
 */
export function withQuestionId(search: string | URLSearchParams | null, questionId: string | null): string {
  const params = new URLSearchParams(search === null || search === undefined ? "" : search);
  if (questionId) params.set("questionId", questionId);
  else params.delete("questionId");
  const q = params.toString();
  return q ? `/add?${q}` : "/add";
}

/**
 * The add-relative page for family `familyId`, sent back to this family's
 * /add when the person is saved (it appends `added=<personId>`). The family
 * id is encoded inside `next` too, so an id with "&" or "#" survives the
 * round trip; for ordinary ids that is exactly `/add?family=<id>`.
 */
export function addRelativeHref(familyId: string): string {
  const back = `/add?family=${encodeURIComponent(familyId)}`;
  return `/families/${encodeURIComponent(familyId)}/people/new?next=${encodeURIComponent(back)}`;
}

/**
 * The people to tick on a form coming back from the add-relative page: the
 * ones carried over (`carried`, null for none) that are still in the family,
 * then `added` when it is someone in the family (`known` ids). An unknown or
 * missing `added` is ignored. Null when there is nobody to tick, so a form
 * with its own people keeps them (see carryInto).
 */
export function preselectAdded(
  carried: readonly string[] | null,
  added: string | null,
  known: readonly string[],
): string[] | null {
  const ids = uniqueStrings((carried ?? []).filter((id) => known.includes(id)));
  if (added && known.includes(added) && !ids.includes(added)) ids.push(added);
  return ids.length ? ids : null;
}

/**
 * The one line on a form whose draft came back from the add-relative page
 * with photos left behind (files cannot be kept across pages). Null when no
 * photos were attached.
 */
export function carriedPhotosNote(photoCount: number): string | null {
  const n = Number.isFinite(photoCount) && photoCount > 0 ? Math.floor(photoCount) : 0;
  if (n === 0) return null;
  return n === 1
    ? "Your story is just as you left it. Your photo didn’t come along, so add it again below."
    : "Your story is just as you left it. Your photos didn’t come along, so add them again below.";
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
// The draft typed so far, carried across a page change: a switch to another
// family (the words and the year; not the people, they belong to the family
// left behind), or a trip to the add-relative page and back (everything but
// the photo files, which cannot be stored: only how many there were). Kept in
// sessionStorage for one read. These helpers never throw.

/** sessionStorage key for the draft carried into a family's form. */
export const CARRY_KEY = "stitch.addCarry";

/**
 * What is carried, for the form of family `familyId`. Only the story and year
 * go to another family; the rest is for coming back to the same one. Carries
 * saved before the optional fields existed read as having none of them.
 */
export interface CarriedStory {
  familyId: string;
  text: string;
  year: string;
  /** People ticked under "Who was there?" (the author may be among them). */
  participantIds?: string[] | null;
  /** The family question picked above the story, by id. */
  questionId?: string | null;
  /** A question typed into the question field (when none was picked). */
  question?: string | null;
  /** How many photos were attached (the files themselves do not come along). */
  photoCount?: number | null;
}

/** A carry as read back: every field present. */
export interface ReadCarry {
  text: string;
  year: string;
  /** Null when no people were carried. */
  participantIds: string[] | null;
  questionId: string | null;
  /** The typed question, "" for none. */
  question: string;
  photoCount: number;
}

function wholeCount(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
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
  const participantIds = uniqueStrings(c.participantIds ?? []);
  const questionId = typeof c.questionId === "string" && c.questionId ? c.questionId : null;
  const question = typeof c.question === "string" ? c.question : "";
  const photoCount = wholeCount(c.photoCount);
  const anything =
    c.text.trim() !== "" ||
    c.year.trim() !== "" ||
    participantIds.length > 0 ||
    questionId !== null ||
    question.trim() !== "" ||
    photoCount > 0;
  try {
    if (!c.familyId || !anything) {
      storage.removeItem(CARRY_KEY);
      return;
    }
    const out: Record<string, unknown> = { familyId: c.familyId, text: c.text, year: c.year };
    if (participantIds.length) out.participantIds = participantIds;
    if (questionId) out.questionId = questionId;
    if (question.trim()) out.question = question;
    if (photoCount) out.photoCount = photoCount;
    storage.setItem(CARRY_KEY, JSON.stringify(out));
  } catch {
    // Not kept: the form starts blank.
  }
}

/**
 * The draft carried into family `familyId`'s form, or null when there is
 * none, it was for another family, or it is unreadable. Reading leaves it in
 * place (React may run a state initializer twice); clearCarry drops it once
 * the form has it, so a reload or a later visit starts blank. Optional fields
 * that are missing (an older carry) or the wrong shape read as none.
 */
export function readCarry(storage: Pick<Storage, "getItem"> | null, familyId: string): ReadCarry | null {
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
  const participantIds = Array.isArray(c.participantIds) ? uniqueStrings(c.participantIds) : [];
  const questionId = typeof c.questionId === "string" && c.questionId ? c.questionId : null;
  const question = typeof c.question === "string" ? c.question : "";
  const photoCount = wholeCount(c.photoCount);
  if (!c.text.trim() && !year && !participantIds.length && !questionId && !question.trim() && !photoCount) {
    return null;
  }
  return {
    text: c.text,
    year,
    participantIds: participantIds.length ? participantIds : null,
    questionId,
    question,
    photoCount,
  };
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
// The draft being typed, kept in sessionStorage for each family while it is
// typed, so a reload of /add in the same tab gives it back. One draft per
// family, cleared once it is sent. Photos are files and cannot be stored:
// only how many there were. These helpers never throw.

/** sessionStorage key: every family's draft, by family id. */
export const DRAFT_KEY = "stitch.addDraft";

/** What is typed on a family's /add form, as kept for a reload. */
export interface TypedDraft {
  familyId: string;
  /** The moment of a second telling opened from `?memoryId=`; null otherwise. */
  memoryId: string | null;
  /** The family question picked above the story; null for none. */
  questionId: string | null;
  text: string;
  /** "" when this form has no year of its own (a second telling). */
  year: string;
  /** People ticked under "Who was there?", the author left out. */
  participantIds: string[];
  /** A question typed into the question field (never a picked one); "" for none. */
  question: string;
  /** How many photos were attached (the files themselves do not come back). */
  photoCount: number;
}

type DraftRecord = Omit<TypedDraft, "familyId">;

function readDraftMap(storage: Pick<Storage, "getItem"> | null): Record<string, unknown> {
  if (!storage) return {};
  let raw: string | null;
  try {
    raw = storage.getItem(DRAFT_KEY);
  } catch {
    return {};
  }
  if (typeof raw !== "string" || !raw) return {};
  try {
    const o: unknown = JSON.parse(raw);
    return typeof o === "object" && o !== null && !Array.isArray(o) ? (o as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function writeDraftMap(storage: Pick<Storage, "setItem" | "removeItem">, map: Record<string, unknown>): void {
  if (Object.keys(map).length === 0) storage.removeItem(DRAFT_KEY);
  else storage.setItem(DRAFT_KEY, JSON.stringify(map));
}

/**
 * Keeps family `d.familyId`'s draft, replacing the one kept before. A draft
 * with nothing typed (no story, year, people, typed question or photos)
 * drops it instead: a picked question alone lives in the URL.
 */
export function saveTypedDraft(
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null,
  d: TypedDraft,
): void {
  if (!storage || !d.familyId) return;
  const participantIds = uniqueStrings(d.participantIds ?? []);
  const photoCount = wholeCount(d.photoCount);
  const text = typeof d.text === "string" ? d.text : "";
  const year = typeof d.year === "string" ? d.year : "";
  const question = typeof d.question === "string" ? d.question : "";
  const anything =
    text.trim() !== "" || year.trim() !== "" || question.trim() !== "" || participantIds.length > 0 || photoCount > 0;
  try {
    const map = readDraftMap(storage);
    if (anything) {
      const record: DraftRecord = {
        memoryId: d.memoryId || null,
        questionId: d.questionId || null,
        text,
        year,
        participantIds,
        question,
        photoCount,
      };
      map[d.familyId] = record;
    } else {
      if (!(d.familyId in map)) return;
      delete map[d.familyId];
    }
    writeDraftMap(storage, map);
  } catch {
    // Not kept: a reload starts blank.
  }
}

/**
 * Family `familyId`'s kept draft, or null when there is none or it is
 * unreadable. A year that is not a year reads as none; wrong-shaped optional
 * fields read as empty.
 */
export function readTypedDraft(storage: Pick<Storage, "getItem"> | null, familyId: string): TypedDraft | null {
  if (!familyId) return null;
  const o = readDraftMap(storage)[familyId];
  if (typeof o !== "object" || o === null || Array.isArray(o)) return null;
  const r = o as Record<string, unknown>;
  if (typeof r.text !== "string") return null;
  const yearRaw = typeof r.year === "string" ? r.year.trim() : "";
  const draft: TypedDraft = {
    familyId,
    memoryId: typeof r.memoryId === "string" && r.memoryId ? r.memoryId : null,
    questionId: typeof r.questionId === "string" && r.questionId ? r.questionId : null,
    text: r.text,
    year: /^\d{1,4}$/.test(yearRaw) ? yearRaw : "",
    participantIds: Array.isArray(r.participantIds) ? uniqueStrings(r.participantIds) : [],
    question: typeof r.question === "string" ? r.question : "",
    photoCount: wholeCount(r.photoCount),
  };
  const anything =
    draft.text.trim() !== "" ||
    draft.year !== "" ||
    draft.question.trim() !== "" ||
    draft.participantIds.length > 0 ||
    draft.photoCount > 0;
  return anything ? draft : null;
}

/** Drops family `familyId`'s kept draft (the others stay). Never throws. */
export function clearTypedDraft(
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null,
  familyId: string,
): void {
  if (!storage || !familyId) return;
  try {
    const map = readDraftMap(storage);
    if (!(familyId in map)) return;
    delete map[familyId];
    writeDraftMap(storage, map);
  } catch {
    // Blocked storage: nothing was kept either.
  }
}

/**
 * The kept draft when it belongs to the form being opened: the same moment
 * (`memoryId`, null for a new memory) and, when the URL names a question,
 * the same question. A draft for another moment or question is not this
 * form's, so the form starts blank (the draft is replaced once typing starts).
 */
export function draftForForm(
  d: TypedDraft | null,
  form: { memoryId: string | null; questionId: string | null },
): TypedDraft | null {
  if (!d) return null;
  if ((d.memoryId ?? null) !== (form.memoryId ?? null)) return null;
  if (form.questionId && d.questionId !== form.questionId) return null;
  return d;
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
// Open runs: the runs started in this tab that have not finished, kept in
// sessionStorage next to their telling slips, so /add can bring the person
// back to one that is waiting on them (a follow-up, a prop review, a merge)
// after they left it. These helpers never throw.

/** sessionStorage key: every open run of this tab, by run id. */
export const OPEN_RUNS_KEY = "stitch.openRuns";

/** What an open run is waiting on the person for; null while it works on its own. */
export type RunWaitKind = "followup" | "props" | "merge";

const WAIT_KINDS: readonly RunWaitKind[] = ["followup", "props", "merge"];

/** The most open runs kept; the oldest are dropped first. */
export const OPEN_RUNS_MAX = 20;

/** A run started in this tab and not yet over. */
export interface OpenRun {
  familyId: string;
  runId: string;
  /** The moment's title for a second telling; null for a new memory (not named yet). */
  title: string | null;
  /** When it started, in ms since the epoch. */
  startedAt: number;
  waiting: RunWaitKind | null;
}

type OpenRunStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function readOpenRunMap(storage: Pick<Storage, "getItem"> | null): Record<string, unknown> {
  if (!storage) return {};
  let raw: string | null;
  try {
    raw = storage.getItem(OPEN_RUNS_KEY);
  } catch {
    return {};
  }
  if (typeof raw !== "string" || !raw) return {};
  try {
    const o: unknown = JSON.parse(raw);
    return typeof o === "object" && o !== null && !Array.isArray(o) ? (o as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function writeOpenRunMap(storage: OpenRunStore, map: Record<string, unknown>): void {
  if (Object.keys(map).length === 0) storage.removeItem(OPEN_RUNS_KEY);
  else storage.setItem(OPEN_RUNS_KEY, JSON.stringify(map));
}

/** One stored entry, checked; null when it is not an open run. */
function openRunOf(runId: string, v: unknown): OpenRun | null {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  if (typeof o.familyId !== "string" || !o.familyId) return null;
  if (typeof o.startedAt !== "number" || !Number.isFinite(o.startedAt)) return null;
  const title = typeof o.title === "string" && o.title.trim() ? o.title.trim() : null;
  const waiting = WAIT_KINDS.includes(o.waiting as RunWaitKind) ? (o.waiting as RunWaitKind) : null;
  return { familyId: o.familyId, runId, title, startedAt: o.startedAt, waiting };
}

/** Keeps run `r.runId` as open (not waiting yet), replacing any record of it. */
export function rememberOpenRun(
  storage: OpenRunStore | null,
  r: { familyId: string; runId: string; title: string | null; startedAt: number },
): void {
  if (!storage || !r.runId || !r.familyId) return;
  try {
    const map = readOpenRunMap(storage);
    const title = typeof r.title === "string" && r.title.trim() ? r.title.trim() : null;
    map[r.runId] = {
      familyId: r.familyId,
      title,
      startedAt: Number.isFinite(r.startedAt) ? r.startedAt : 0,
      waiting: null,
    };
    // Only the newest are kept: a tab that starts many runs never grows this without end.
    const ids = Object.keys(map);
    if (ids.length > OPEN_RUNS_MAX) {
      const byAge = ids
        .map((id) => ({ id, at: openRunOf(id, map[id])?.startedAt ?? -Infinity }))
        .sort((a, b) => b.at - a.at);
      for (const { id } of byAge.slice(OPEN_RUNS_MAX)) delete map[id];
    }
    writeOpenRunMap(storage, map);
  } catch {
    // Not kept: /add will not offer a way back to it.
  }
}

/**
 * Says what open run `runId` is waiting on (null: nothing, it is working). A
 * run this tab does not remember is left alone: it was never started here,
 * or it is already over.
 */
export function markRunWaiting(storage: OpenRunStore | null, runId: string, kind: RunWaitKind | null): void {
  if (!storage || !runId) return;
  try {
    const map = readOpenRunMap(storage);
    const run = openRunOf(runId, map[runId]);
    if (!run) return;
    const waiting = kind && WAIT_KINDS.includes(kind) ? kind : null;
    if (run.waiting === waiting) return;
    map[runId] = { familyId: run.familyId, title: run.title, startedAt: run.startedAt, waiting };
    writeOpenRunMap(storage, map);
  } catch {
    // Blocked storage: nothing was kept either.
  }
}

/** Drops open run `runId` (it finished, failed, or the server no longer knows it). */
export function forgetOpenRun(storage: OpenRunStore | null, runId: string): void {
  if (!storage || !runId) return;
  try {
    const map = readOpenRunMap(storage);
    if (!(runId in map)) return;
    delete map[runId];
    writeOpenRunMap(storage, map);
  } catch {
    // Blocked storage: nothing was kept either.
  }
}

/** Family `familyId`'s open runs, newest first. Unreadable entries are skipped. */
export function openRunsFor(storage: Pick<Storage, "getItem"> | null, familyId: string): OpenRun[] {
  if (!familyId) return [];
  const map = readOpenRunMap(storage);
  const out: OpenRun[] = [];
  for (const [runId, v] of Object.entries(map)) {
    const run = openRunOf(runId, v);
    if (run && run.familyId === familyId) out.push(run);
  }
  return out.sort((a, b) => b.startedAt - a.startedAt);
}

/**
 * The one open run /add offers to go back to: the newest that is waiting on
 * the person, else the newest. Null for none.
 */
export function runToResume(runs: readonly OpenRun[]): OpenRun | null {
  return runs.find((r) => r.waiting !== null) ?? runs[0] ?? null;
}

/** The first `n` words of `text`, with "…" when there were more; null when blank. */
export function firstWords(text: string | null | undefined, n = 6): string | null {
  if (typeof text !== "string") return null;
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  const head = words.slice(0, n).join(" ");
  return words.length > n ? `${head.replace(/[.,;:!?]+$/, "")}…` : head;
}

/**
 * The calm note above /add's form about an open run: its title (a second
 * telling's moment), else the first words of the story sent (`storyText`, from
 * the telling slip), then whether it waits on the person.
 */
export function openRunNotice(run: OpenRun, storyText: string | null | undefined): string {
  const name = run.title ?? firstWords(storyText);
  const subject = name ? `Your memory ‘${name}’` : "Your memory";
  return run.waiting ? `${subject} is waiting for your answer.` : `${subject} is still being made.`;
}

// ---------------------------------------------------------------------------
// Which family holds a moment

function isNotFound(e: unknown): boolean {
  return statusOf(e) === 404;
}

/**
 * The first family in `familyIds` (in order: the remembered one first) whose
 * `getMemory` finds the moment, with what it returned. A 404 moves on to the
 * next family; any other failure rejects. Null when no family has it.
 */
export async function findMomentFamily<T>(
  familyIds: readonly string[],
  getMemory: (familyId: string) => Promise<T>,
): Promise<{ familyId: string; memory: T } | null> {
  for (const familyId of familyIds) {
    try {
      const memory = await getMemory(familyId);
      return { familyId, memory };
    } catch (e) {
      if (!isNotFound(e)) throw e;
    }
  }
  return null;
}

/**
 * The first of `others` (in order) with an open question for this person,
 * from what GET /questions gave for each (null where it failed). Null when
 * nobody elsewhere asked anything.
 */
export function familyAsking<F>(
  others: readonly F[],
  questions: readonly (readonly unknown[] | null | undefined)[],
): F | null {
  for (let i = 0; i < others.length; i++) {
    const qs = questions[i];
    if (Array.isArray(qs) && qs.length > 0) return others[i];
  }
  return null;
}

// ---------------------------------------------------------------------------
// Carrying a draft into a remounted form

/** What the add page carries from one form into the next (photos travel apart). */
export interface CarriedDraft {
  text: string;
  year: string;
  /** People picked for a new memory; null when none were chosen (a second telling). */
  participantIds: string[] | null;
  /**
   * A question typed into the question field, brought back from the
   * add-relative page. A picked question travels by id instead (the page
   * picks it again), so this is only used when the form has no question.
   */
  question?: string | null;
  /** The picked question's id (the page re-picks it; carryInto ignores it). */
  questionId?: string | null;
}

/**
 * The starting values for a form that replaces another (a picked question, a
 * released one, another family's form, the same form back from the
 * add-relative page), with what was typed carried in: the story always; the
 * year, people and a typed question only into a new memory that has none of
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
    const typedQuestion = typeof carried.question === "string" ? carried.question : "";
    if (typedQuestion.trim() && !(initial?.question ?? "").trim()) out.question = typedQuestion;
  }
  return Object.keys(out).length ? { ...initial, ...out } : initial;
}
