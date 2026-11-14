// The pure half of /ask ("Ask the family"): the question in the URL, what the
// answer points at (the best match, the other quotes grouped by moment), the
// page's state machine with its stale-response guard, the links it opens and
// a small per-tab cache so Back and reload do not ask the model again.
//
// No runtime imports and only erasable TypeScript, so `node --test` loads this
// file directly. Tests: components/flow/__tests__/askPage.test.mjs.

import type { AskQuote, AskResponse } from "@/lib/types";

/** The longest question the page sends (the field's maxLength): api/routes/ask.py MAX_QUERY_CHARS. */
export const ASK_MAX_LENGTH = 1000;

/** Example questions shown as chips under the field. */
export const ASK_EXAMPLES: readonly string[] = [
  "How did David and Ana meet at church?",
  "Who learned to ride a bike?",
  "What happened on the road trip?",
];

// ---------------------------------------------------------------------------
// The URL

export interface AskQuery {
  familyId: string | null;
  /** The question, trimmed; null when missing or blank. */
  q: string | null;
}

function paramsOf(search: string | URLSearchParams | null | undefined): URLSearchParams {
  if (search === null || search === undefined) return new URLSearchParams();
  return typeof search === "string" ? new URLSearchParams(search) : search;
}

/** ?family= and ?q= of an /ask URL. */
export function readAskQuery(search: string | URLSearchParams | null | undefined): AskQuery {
  const params = paramsOf(search);
  const family = params.get("family");
  return { familyId: family ? family : null, q: cleanQuestion(params.get("q")) };
}

/**
 * A question as the page sends it: whitespace collapsed, trimmed, cut to
 * ASK_MAX_LENGTH. Null when nothing is left.
 */
export function cleanQuestion(raw: string | null | undefined): string | null {
  if (typeof raw !== "string") return null;
  const q = raw.replace(/\s+/g, " ").trim().slice(0, ASK_MAX_LENGTH).trim();
  return q ? q : null;
}

/** Two questions are the same search when they clean to the same text (case aside). */
export function sameQuestion(a: string | null | undefined, b: string | null | undefined): boolean {
  const x = cleanQuestion(a);
  const y = cleanQuestion(b);
  return x !== null && y !== null && x.toLowerCase() === y.toLowerCase();
}

// ---------------------------------------------------------------------------
// Links

const enc = encodeURIComponent;

/**
 * A moment's book page, opened straight at its spread (`?state=open`, not the
 * closed-book intro) and, when known, starting with the telling that was
 * quoted (`?narrator=<teller's person id>`; the book ignores an id it does
 * not know). The same path and `?family=` as hrefs.memory in session.ts.
 */
export function memoryPageHref(o: { memoryId: string; familyId?: string | null; narrator?: string | null }): string {
  const params: string[] = [];
  if (o.familyId) params.push(`family=${enc(o.familyId)}`);
  params.push("state=open");
  if (o.narrator) params.push(`narrator=${enc(o.narrator)}`);
  return `/memories/${enc(o.memoryId)}?${params.join("&")}`;
}

// ---------------------------------------------------------------------------
// What the answer points at

/** One moment the answer quotes, with its quotes in the order they came. */
export interface QuoteGroup {
  memoryId: string;
  title: string | null;
  year: number | null;
  quotes: AskQuote[];
}

/** The memory to open, whether the server named it or the page fell back to the first quote's. */
export interface BestMatch {
  memoryId: string;
  title: string | null;
  year: number | null;
  /** The person whose telling is quoted (or whose book holds it); may be null. */
  personId: string | null;
  /** Clearly ahead of the other candidates: Enter opens it. */
  confident: boolean;
  /** The quotes from this memory, first first. */
  quotes: AskQuote[];
}

function text(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

function yearOf(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** The quotes a response carries, dropping any without a memory id or text. */
export function quotesOf(res: AskResponse | null | undefined): AskQuote[] {
  const list = Array.isArray(res?.quotes) ? res.quotes : [];
  return list.filter((q) => q && text(q.memoryId) !== null && text(q.text) !== null);
}

/**
 * The quotes grouped by moment, in the order each moment first appears. A
 * moment's title and year come from the first of its quotes that has them.
 */
export function groupQuotes(quotes: readonly AskQuote[]): QuoteGroup[] {
  const groups = new Map<string, QuoteGroup>();
  for (const q of quotes) {
    let g = groups.get(q.memoryId);
    if (!g) {
      g = { memoryId: q.memoryId, title: null, year: null, quotes: [] };
      groups.set(q.memoryId, g);
    }
    g.title ??= text(q.title);
    g.year ??= yearOf(q.year);
    g.quotes.push(q);
  }
  return [...groups.values()];
}

/**
 * The memory to open. The server's `best` when it sends one; `best: null`
 * from a server that fills `mode` means nothing fits. A server that does not
 * pick one yet (no `best`, or the schema's default null with no `mode`)
 * gets the first quote's memory, never confident. Title and year fill in
 * from the quotes when missing.
 */
export function bestMatch(res: AskResponse | null | undefined): BestMatch | null {
  if (!res) return null;
  const quotes = quotesOf(res);
  const groups = groupQuotes(quotes);
  const best = res.best;
  if (best === null && (res.mode === "search" || res.mode === "model")) return null;
  if (best && text(best.memoryId)) {
    const g = groups.find((x) => x.memoryId === best.memoryId);
    return {
      memoryId: best.memoryId,
      title: text(best.title) ?? g?.title ?? null,
      year: yearOf(best.year) ?? g?.year ?? null,
      personId: text(best.personId) ?? g?.quotes[0]?.authorId ?? null,
      confident: best.confident === true,
      quotes: g?.quotes ?? [],
    };
  }
  const first = groups[0];
  if (!first) return null;
  return {
    memoryId: first.memoryId,
    title: first.title,
    year: first.year,
    personId: text(first.quotes[0]?.authorId) ?? null,
    confident: false,
    quotes: first.quotes,
  };
}

/** The other moments the answer quotes: every group but the best match's. */
export function otherGroups(res: AskResponse | null | undefined, best: BestMatch | null): QuoteGroup[] {
  const groups = groupQuotes(quotesOf(res));
  return best ? groups.filter((g) => g.memoryId !== best.memoryId) : groups;
}

/**
 * Where "Open this page" goes: the best memory's book page, starting with the
 * quoted telling's teller when there is a quote (else no narrator: `personId`
 * may be a participant who told nothing).
 */
export function bestHref(best: BestMatch, familyId: string | null): string {
  return memoryPageHref({ memoryId: best.memoryId, familyId, narrator: best.quotes[0]?.authorId ?? null });
}

/** A moment's title for the page, or a quiet stand-in. */
export function momentTitle(title: string | null | undefined): string {
  return text(title) ?? "A family moment";
}

/** "Rosa", else "Someone in the family", from a person id → name map. */
export function tellerName(names: ReadonlyMap<string, string> | null, personId: string | null | undefined): string {
  const name = personId && names ? names.get(personId) : undefined;
  return name && name.trim() ? name.trim() : "Someone in the family";
}

/** The answer sentence, or a plain stand-in when the server sent none. */
export function answerText(res: AskResponse, best: BestMatch | null): string {
  const a = text(res.answer);
  if (a) return a;
  return best ? "This is the closest page in the family book." : NOTHING_FOUND;
}

export const NOTHING_FOUND = "No one in the family has told that story yet.";

// ---------------------------------------------------------------------------
// The state machine

export type AskView =
  | { kind: "idle" }
  /** `shown` keeps the last result on screen (dimmed) so nothing jumps. */
  | { kind: "searching"; q: string; seq: number; shown: AskResult | null }
  | { kind: "results"; q: string; seq: number; res: AskResponse; at: number }
  | { kind: "nothing"; q: string; seq: number; res: AskResponse; at: number }
  | { kind: "error"; q: string; seq: number; message: string };

/** A view with an answer on it. */
export type AskResult = Extract<AskView, { kind: "results" | "nothing" }>;

export type AskAction =
  | { type: "start"; q: string; seq: number }
  /** `at`: when the answer arrived (ms), for the open-on-Enter guard. */
  | { type: "done"; seq: number; res: AskResponse; at: number }
  | { type: "failed"; seq: number; message: string }
  | { type: "clear" };

export const ASK_IDLE: AskView = { kind: "idle" };

/** Nothing fits: no best match and no quotes. */
export function isNothing(res: AskResponse): boolean {
  return bestMatch(res) === null && quotesOf(res).length === 0;
}

/**
 * The page's state. Each search has a sequence number; an answer or failure
 * for any search but the latest is ignored, so a slow earlier search never
 * overwrites a newer one.
 */
export function askReducer(view: AskView, action: AskAction): AskView {
  switch (action.type) {
    case "start": {
      const shown: AskResult | null =
        view.kind === "results" || view.kind === "nothing" ? view : view.kind === "searching" ? view.shown : null;
      return { kind: "searching", q: action.q, seq: action.seq, shown };
    }
    case "done":
      if (view.kind !== "searching" || view.seq !== action.seq) return view;
      return {
        kind: isNothing(action.res) ? "nothing" : "results",
        q: view.q,
        seq: view.seq,
        res: action.res,
        at: action.at,
      };
    case "failed":
      if (view.kind !== "searching" || view.seq !== action.seq) return view;
      return { kind: "error", q: view.q, seq: view.seq, message: action.message };
    case "clear":
      return ASK_IDLE;
  }
}

/** How long after an answer lands Enter still will not open it: a double press must not navigate unseen. */
export const OPEN_GUARD_MS = 400;

/** What a submit does with `typed` while the page shows `view`. */
export type SubmitIntent =
  | { kind: "none" }
  /** Enter on the same question, answered with a confident match: open it. */
  | { kind: "open"; href: string }
  /** The same question again (answered, nothing found or failed): ask the server afresh. */
  | { kind: "reask"; q: string }
  /** The same question, on its way (or answered a moment ago): do nothing. */
  | { kind: "stay" }
  /** A new question: put it in the URL. */
  | { kind: "ask"; q: string };

/**
 * What a submit does. `via` is how it was sent: Enter in the field (which
 * opens a confident match once it has been on screen OPEN_GUARD_MS) or the
 * Ask button (which always asks). `now` is the clock in ms.
 */
export function submitIntent(
  view: AskView,
  typed: string,
  familyId: string | null,
  via: "enter" | "button",
  now: number,
): SubmitIntent {
  const q = cleanQuestion(typed);
  if (!q) return { kind: "none" };
  if (view.kind === "idle" || !sameQuestion(view.q, q)) return { kind: "ask", q };
  if (view.kind === "searching") return { kind: "stay" };
  if (view.kind === "results" && via === "enter") {
    const best = bestMatch(view.res);
    if (best?.confident) {
      if (now - view.at < OPEN_GUARD_MS) return { kind: "stay" };
      return { kind: "open", href: bestHref(best, familyId) };
    }
  }
  return { kind: "reask", q: view.q };
}

/**
 * Whether a search may show a kept answer: only when the page was reached by
 * Back or forward (a popstate within `windowMs`, or a document loaded by
 * history traversal), never on a reload or a new question.
 */
export function fromHistory(o: { now: number; lastPopAt: number | null; docTraversal: boolean; windowMs?: number }): boolean {
  if (o.docTraversal) return true;
  return o.lastPopAt !== null && o.now - o.lastPopAt >= 0 && o.now - o.lastPopAt <= (o.windowMs ?? 2000);
}

/**
 * The /ask URL the page should show for what it actually asked: the family
 * it settled on and the cleaned question. Null when the URL already says so.
 */
export function canonicalAskHref(search: string | URLSearchParams | null | undefined, familyId: string): string | null {
  const params = paramsOf(search);
  const rawQ = params.get("q");
  const q = cleanQuestion(rawQ);
  const sameFamily = params.get("family") === familyId;
  const sameQ = q === null ? !rawQ : rawQ === q;
  if (sameFamily && sameQ) return null;
  const parts = [`family=${enc(familyId)}`];
  if (q) parts.push(`q=${enc(q)}`);
  return `/ask?${parts.join("&")}`;
}

/** The one quiet line under the field: what Enter will do next. */
export function fieldHint(view: AskView, typed: string): string {
  if (view.kind === "searching" && sameQuestion(view.q, typed)) return "Looking through the family’s pages…";
  if (view.kind === "error" && sameQuestion(view.q, typed)) return "Press Enter to try again.";
  if (view.kind === "results" && sameQuestion(view.q, typed) && bestMatch(view.res)?.confident) {
    return "Found it. Press Enter again to open it.";
  }
  return "In your own words. Names, places and years help.";
}

// ---------------------------------------------------------------------------
// The per-tab cache: an answer kept for this tab, so Back from the book page
// shows it at once instead of asking again (the page reads it only on
// history traversal: see fromHistory). "Nothing found" is never kept, so a
// story told since is found the next time. Entries are scoped to the user
// and family (askCacheScope), and sign-out clears them. Storage can be
// missing or throw (private mode, blocked site data); these never throw.

export const ASK_CACHE_KEY = "stitch.ask";
/** Answers kept per tab, newest first. */
export const ASK_CACHE_MAX = 8;

/** Who and which family a kept answer belongs to. */
export function askCacheScope(userId: string, familyId: string): string {
  return `${userId}\u0000${familyId}`;
}

interface CacheEntry {
  /** askCacheScope(user, family). */
  familyId: string;
  q: string;
  res: AskResponse;
}

type StorageLike = Pick<Storage, "getItem" | "setItem">;

/** Forget every kept answer (sign-out). */
export function clearAskCache(store: Pick<Storage, "removeItem"> | null): void {
  try {
    store?.removeItem(ASK_CACHE_KEY);
  } catch {
    // Blocked storage: nothing was kept there either.
  }
}

function readCache(store: StorageLike | null): CacheEntry[] {
  if (!store) return [];
  try {
    const raw = store.getItem(ASK_CACHE_KEY);
    const list: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(list)) return [];
    return list.filter(
      (e): e is CacheEntry =>
        !!e &&
        typeof e.familyId === "string" &&
        typeof e.q === "string" &&
        !!e.res &&
        typeof e.res === "object" &&
        Array.isArray(e.res.quotes),
    );
  } catch {
    return [];
  }
}

/** The answer kept for this scope (askCacheScope) and question, or null. */
export function cachedAnswer(store: StorageLike | null, scope: string, q: string): AskResponse | null {
  const res = readCache(store).find((e) => e.familyId === scope && sameQuestion(e.q, q))?.res ?? null;
  return res && !isNothing(res) ? res : null;
}

/** Keep this answer (newest first, at most ASK_CACHE_MAX). "Nothing found" is not kept. */
export function cacheAnswer(store: StorageLike | null, scope: string, q: string, res: AskResponse): void {
  if (!store || isNothing(res)) return;
  const familyId = scope;
  const rest = readCache(store).filter((e) => !(e.familyId === familyId && sameQuestion(e.q, q)));
  try {
    store.setItem(ASK_CACHE_KEY, JSON.stringify([{ familyId, q, res }, ...rest].slice(0, ASK_CACHE_MAX)));
  } catch {
    // Full or blocked: the page just asks again next time.
  }
}
