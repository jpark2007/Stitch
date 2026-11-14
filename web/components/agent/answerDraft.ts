// What the family has typed into a pause card but not sent yet, kept in
// sessionStorage so a reload (or the tab being thrown away and restored) does
// not lose it. No runtime imports and only erasable TypeScript, so
// `node --test` can load this file directly.
//
// One storage key holds every run's drafts, as JSON:
//   [[runId, { [pauseKey]: text }], ...]   oldest run first
// A run moves to the end whenever one of its drafts is saved, and only the
// last MAX_DRAFT_RUNS runs are kept. Every storage call is wrapped: a full,
// blocked or missing storage only means the draft is not kept.

/** The sessionStorage key every run's drafts live under. */
export const ANSWER_DRAFT_KEY = "stitch.answerDraft";

/** Most runs whose drafts are kept; the run saved longest ago goes first. */
export const MAX_DRAFT_RUNS = 10;

/**
 * Where a card's draft is kept: the run (useAgentRun's runKey, family and run
 * id) and the pause within it (see draftPauseKey).
 */
export interface AnswerDraftTarget {
  runId: string;
  pauseKey: string;
}

/**
 * The answer callback a card is given. useAgentRun tags it with the draft
 * target of the pause it answers, so a card can keep its draft without the
 * page that renders it passing anything more.
 */
export type DraftedAnswer = ((text: string) => Promise<void>) & { readonly draft?: AnswerDraftTarget };

/**
 * The key for one pause of a run: its kind and the sequence number of its own
 * log entry (RunState.pauseSeq), which the replay after a reload gives the
 * same pause again.
 */
export function draftPauseKey(kind: string, pauseSeq: number): string {
  return `${kind}-${pauseSeq}`;
}

type DraftRuns = [string, Record<string, string>][];

function readAll(storage: Storage | null | undefined): DraftRuns {
  if (!storage) return [];
  let raw: string | null;
  try {
    raw = storage.getItem(ANSWER_DRAFT_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(data)) return [];
  const out: DraftRuns = [];
  for (const item of data) {
    if (!Array.isArray(item) || item.length !== 2) continue;
    const [runId, drafts] = item as unknown[];
    if (typeof runId !== "string" || !runId) continue;
    if (typeof drafts !== "object" || drafts === null || Array.isArray(drafts)) continue;
    const clean: Record<string, string> = {};
    for (const [key, text] of Object.entries(drafts as Record<string, unknown>)) {
      if (typeof text === "string" && text) clean[key] = text;
    }
    if (Object.keys(clean).length > 0) out.push([runId, clean]);
  }
  return out;
}

function writeAll(storage: Storage, runs: DraftRuns): void {
  const kept = runs.slice(-MAX_DRAFT_RUNS);
  try {
    if (kept.length === 0) storage.removeItem(ANSWER_DRAFT_KEY);
    else storage.setItem(ANSWER_DRAFT_KEY, JSON.stringify(kept));
  } catch {
    // Full or blocked: the draft is simply not kept.
  }
}

/**
 * Keeps `text` as the draft for one pause of a run, and makes that run the
 * most recent. An empty text removes the draft (and the run, once it has no
 * drafts left).
 */
export function saveAnswerDraft(
  storage: Storage | null | undefined,
  runId: string,
  pauseKey: string,
  text: string,
): void {
  if (!storage || !runId) return;
  const runs = readAll(storage);
  const at = runs.findIndex(([id]) => id === runId);
  const drafts: Record<string, string> = at >= 0 ? { ...runs[at][1] } : {};
  if (at >= 0) runs.splice(at, 1);
  if (text) drafts[pauseKey] = text;
  else delete drafts[pauseKey];
  if (Object.keys(drafts).length > 0) runs.push([runId, drafts]);
  else if (at < 0) return; // nothing was kept and nothing is: leave storage alone
  writeAll(storage, runs);
}

/** The draft kept for one pause of a run, or "" when there is none. */
export function readAnswerDraft(storage: Storage | null | undefined, runId: string, pauseKey: string): string {
  const run = readAll(storage).find(([id]) => id === runId);
  return run?.[1][pauseKey] ?? "";
}

/** Forgets one run's drafts, or every run's when no run is named. */
export function clearAnswerDraft(storage: Storage | null | undefined, runId?: string): void {
  if (!storage) return;
  if (runId === undefined) {
    try {
      storage.removeItem(ANSWER_DRAFT_KEY);
    } catch {
      // Nothing more to do.
    }
    return;
  }
  const runs = readAll(storage);
  const left = runs.filter(([id]) => id !== runId);
  if (left.length !== runs.length) writeAll(storage, left);
}

/** sessionStorage in a browser, or null where it is missing or blocked. */
export function sessionDraftStore(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}
