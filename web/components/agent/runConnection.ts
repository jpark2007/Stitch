// Pure connection policy for the live agent feed: retry timing, when to
// reconnect, the summaries logged after an answer, and the stream-following
// loop itself with every side effect injected. No runtime imports and only
// erasable TypeScript, so `node --test` can load this file directly.
import type { AnswerRequest, PropEdit } from "@/lib/types";
import type { RunAction, RunEventMessage } from "./runState";

export type AnswerKind = AnswerRequest["kind"];

/** Reconnects allowed after a dropped stream before the feed gives up. */
export const DEFAULT_MAX_RETRIES = 5;

/** Longest wait between reconnects. */
export const MAX_RETRY_DELAY_MS = 8000;

/**
 * A connection that stays open at least this long counts as healthy even if
 * it brought nothing new (the run may simply be waiting on the family), so a
 * later drop starts a fresh run of retries.
 */
export const HEALTHY_CONNECTION_MS = 20000;

const SUMMARY_MAX = 80;

/** Wait before reconnect `attempt` (1-based): 1s, 2s, 4s, then 8s from the 4th on. */
export function defaultRetryDelay(attempt: number): number {
  const n = Number.isFinite(attempt) ? Math.max(1, Math.floor(attempt)) : 1;
  // 2^(n-1) passes the cap by n = 5; stop there so huge attempts never overflow.
  if (n >= 5) return MAX_RETRY_DELAY_MS;
  return Math.min(MAX_RETRY_DELAY_MS, 1000 * 2 ** (n - 1));
}

/**
 * True when the feed is in a terminal phase, so nothing will reconnect on its
 * own and only `retry()` starts it again. That is phase "done", or phase
 * "error". Phase "error" comes either from the run's own `error` event (the
 * last log entry is that error) or from giving up on the connection; both
 * stop the stream for good, and RunState does not tell them apart, so the
 * rule is decided on the phase alone.
 */
export function isFinished(state: { phase: string }): boolean {
  return state.phase === "done" || state.phase === "error";
}

/** Run events after which the server sends nothing more: stop reading. */
export function isFinalEvent(event: string): boolean {
  return event === "done" || event === "error";
}

/**
 * After a final event, how long followRun keeps reading for the end of the
 * stream before it closes the connection itself. The server closes the stream
 * right after `done`/`error`, but a replayed stream may still be on its way;
 * cutting it off shows up as an aborted request (ERR_ABORTED) in the browser.
 */
export const FINISH_DRAIN_MS = 1500;

/**
 * The short record of an accepted answer, kept on the log's `answered` entry.
 * feedView.answeredLine phrases it per kind for the family.
 */
export function answerSummary(body: AnswerRequest): string {
  switch (body.kind) {
    case "answer":
      return truncate((body.text ?? "").replace(/\s+/g, " ").trim(), SUMMARY_MAX) || "No answer";
    case "props":
      return propsSummary(body.props ?? []);
    case "merge":
      return body.approve ? "Yes, the same person" : "No, different people";
    default:
      return "Sent";
  }
}

/**
 * User-safe sentence shown when the stream cannot be reached after every
 * retry. `attempts` (failed connections in a row) is accepted for callers
 * that log it; the family sees the same calm sentence however many there were.
 */
export function connectionErrorMessage(attempts: number): string {
  void attempts;
  return "We lost touch with the studio. Try again in a moment.";
}

function truncate(text: string, max: number): string {
  // Count code points so an emoji is never cut in half.
  const chars = Array.from(text);
  if (chars.length <= max) return text;
  return chars.slice(0, max - 1).join("").trimEnd() + "…";
}

const PROP_PHRASES: { action: PropEdit["action"]; verb: string; tail: string }[] = [
  { action: "keep", verb: "kept", tail: "" },
  { action: "rename", verb: "renamed", tail: "" },
  { action: "remove", verb: "removed", tail: "" },
  { action: "replace_photo", verb: "replaced", tail: " with a photo" },
];

/** "Kept 2 props, renamed 1, removed 1"; "No changes" for an empty list. */
function propsSummary(edits: PropEdit[]): string {
  const parts: string[] = [];
  for (const { action, verb, tail } of PROP_PHRASES) {
    const n = edits.filter((e) => e.action === action).length;
    if (n === 0) continue;
    // Only the first phrase names the noun: "Kept 2 props, renamed 1".
    const noun = parts.length === 0 ? ` ${n === 1 ? "prop" : "props"}` : "";
    parts.push(`${verb} ${n}${noun}${tail}`);
  }
  if (parts.length === 0) return "No changes";
  const line = parts.join(", ");
  return line.charAt(0).toUpperCase() + line.slice(1);
}

// ---------------------------------------------------------------------------
// Reconnect policy

export type ConnectionEnd =
  /** A `done` or `error` event arrived: the run is over. */
  | "finished"
  /** The caller aborted (unmount, source change, retry). */
  | "aborted"
  /** The stream threw or closed before the run was over. */
  | "dropped";

export interface ConnectionOutcome {
  ended: ConnectionEnd;
  /** Failed connections in a row before this one. */
  failures: number;
  /** Whether this connection was healthy (see connectionMadeProgress). */
  progressed: boolean;
}

export type ConnectionStep =
  | { kind: "stop" }
  /** Reconnect after `delayMs`; `failures` is the new count of failures in a row. */
  | { kind: "retry"; attempt: number; failures: number; delayMs: number }
  /** Out of retries: dispatch connection_error with `message`. */
  | { kind: "give_up"; failures: number; message: string };

/**
 * What to do after a connection ends. A drop after a healthy connection
 * starts counting again from 1; otherwise failures accumulate. Reconnect
 * `attempt` n waits retryDelay(n); once n would pass maxRetries, give up.
 */
export function nextConnectionStep(
  outcome: ConnectionOutcome,
  maxRetries: number = DEFAULT_MAX_RETRIES,
  retryDelay: (attempt: number) => number = defaultRetryDelay,
): ConnectionStep {
  if (outcome.ended !== "dropped") return { kind: "stop" };
  const failures = outcome.progressed ? 1 : Math.max(0, outcome.failures) + 1;
  const limit = Number.isNaN(maxRetries) ? 0 : Math.max(0, maxRetries);
  if (failures > limit) {
    return { kind: "give_up", failures, message: connectionErrorMessage(failures) };
  }
  const raw = retryDelay(failures);
  const delayMs = Number.isFinite(raw) ? Math.max(0, raw) : MAX_RETRY_DELAY_MS;
  return { kind: "retry", attempt: failures, failures, delayMs };
}

/**
 * A connection was healthy if it folded more run events than any earlier
 * connection for this run (with replay, the replayed prefix is not news), or
 * if it stayed open for HEALTHY_CONNECTION_MS.
 */
export function connectionMadeProgress(p: {
  highWaterBefore: number;
  foldedAfter: number;
  connectedMs: number;
}): boolean {
  return p.foldedAfter > p.highWaterBefore || p.connectedMs >= HEALTHY_CONNECTION_MS;
}

// ---------------------------------------------------------------------------
// Following one run

/** An answer the family gave, re-applied at the same point when the run is replayed. */
export interface AnsweredRecord {
  /** How many run events had been folded when the answer was accepted. */
  after: number;
  kind: AnswerKind;
  /** null: the server said the pause was already answered (see sendAnswer). */
  summary: string | null;
}

/** Book-keeping for one run, kept across reconnects and retries of the same source. */
export interface RunBook {
  /** Run events folded into the current state since its last reset. */
  folded: number;
  /** Most run events ever folded for this run. */
  highWater: number;
  answers: AnsweredRecord[];
}

/**
 * A fresh book. `answers` are the ones saved for this run before a reload
 * (see parseAnswers): the replay then closes each answered pause right after
 * the event it answered, as if the answer had been given on this page.
 */
export function newRunBook(answers: AnsweredRecord[] = []): RunBook {
  return { folded: 0, highWater: 0, answers: answers.map((a) => ({ ...a })) };
}

// ---------------------------------------------------------------------------
// Saving answers across a reload
//
// The run's stream replays every event from the first, but no run event says
// that a pause was answered: after a reload the replay ends on the question
// until the run's next step closes it (see runState clearsPause). So the hook
// keeps the answers this tab gave in sessionStorage, per run, and a reload in
// the same tab re-applies them at the point they were given. Another tab or
// device still sees the pause until the run moves on; answering it there
// gets a 409 NOT_WAITING, which closes it quietly (sendAnswer).

/** Most answers kept per run; a run pauses a handful of times at most. */
export const MAX_SAVED_ANSWERS = 50;

const ANSWER_KINDS: readonly string[] = ["answer", "props", "merge"];

/** The sessionStorage key for one run's answers. */
export function answersStorageKey(runKey: string): string {
  return `stitch:run-answers:${runKey}`;
}

/** The book's answers as stored text. */
export function serializeAnswers(answers: readonly AnsweredRecord[]): string {
  return JSON.stringify(
    answers.slice(-MAX_SAVED_ANSWERS).map(({ after, kind, summary }) => ({ after, kind, summary })),
  );
}

/**
 * Saved answers read back, keeping only well-formed records (a positive whole
 * `after`, a known kind, a string or null summary). Anything unreadable gives
 * an empty list, so a bad entry can only mean the card shows again.
 */
export function parseAnswers(raw: string | null | undefined): AnsweredRecord[] {
  if (!raw) return [];
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(data)) return [];
  const out: AnsweredRecord[] = [];
  for (const item of data.slice(-MAX_SAVED_ANSWERS)) {
    if (typeof item !== "object" || item === null) continue;
    const { after, kind, summary } = item as Record<string, unknown>;
    if (typeof after !== "number" || !Number.isInteger(after) || after < 1) continue;
    if (typeof kind !== "string" || !ANSWER_KINDS.includes(kind)) continue;
    if (summary !== null && typeof summary !== "string") continue;
    out.push({ after, kind: kind as AnswerKind, summary: summary === null ? null : truncate(summary, SUMMARY_MAX) });
  }
  return out;
}

/**
 * Notes an accepted answer and returns the `answered` action to dispatch for
 * it. `sentAt` is `book.folded` when the answer was posted: if a replay reset
 * the count while the post was in flight, the answer still lands after the
 * events it was given in reply to.
 */
export function recordAnswer(
  book: RunBook,
  kind: AnswerKind,
  summary: string | null,
  sentAt: number = book.folded,
): RunAction {
  book.answers.push({ after: Math.max(sentAt, book.folded), kind, summary });
  return { type: "answered", kind, summary };
}

/** The AnswerRequest each answer helper posts. */
export function answerBody(kind: "answer", value: string): AnswerRequest;
export function answerBody(kind: "props", value: PropEdit[]): AnswerRequest;
export function answerBody(kind: "merge", value: boolean): AnswerRequest;
export function answerBody(kind: AnswerKind, value: string | PropEdit[] | boolean): AnswerRequest {
  if (kind === "answer") return { kind, text: value as string };
  if (kind === "props") return { kind, props: value as PropEdit[] };
  return { kind, approve: value as boolean };
}

/**
 * The answer route's 409 detail when the run has moved past the pause
 * (api/routes/memories.py NOT_WAITING). Other 409s from that route say the
 * answer itself did not fit (ANSWER_CONFLICT, a wrong kind, an empty text).
 */
export const NOT_WAITING_DETAIL = "This run is not waiting for an answer";

/** Older servers and the mock backend word it differently ("isn't waiting for that"). */
const NOT_WAITING_PATTERN = /\b(?:not|isn['’]?t)\s+waiting\b/i;

/** The 409 detail for "An answer needs text". */
const NEEDS_TEXT_PATTERN = /needs\s+text/i;

/** The server's sentence on an API client error: `detail`, else `message`. */
function detailOf(error: unknown): string {
  if (typeof error !== "object" || error === null) return "";
  const { detail, message } = error as { detail?: unknown; message?: unknown };
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  return typeof message === "string" ? message.trim() : "";
}

/**
 * True for a rejection that says the run is not waiting for an answer any
 * more: HTTP 409 with the NOT_WAITING detail (or, from an older server or the
 * mock, a detail that says "not waiting"). Only that closes a card quietly;
 * any other 409 means the answer did not fit and the card stays. Duck-typed
 * on `status` and `detail`/`message`, so this module needs no runtime import
 * of the API client's error class.
 */
export function isNotWaiting(error: unknown): boolean {
  if (statusOf(error) !== 409) return false;
  const detail = detailOf(error);
  return detail === NOT_WAITING_DETAIL || NOT_WAITING_PATTERN.test(detail);
}

/** What the family is sending, in the words of a failed-send message. */
const ANSWER_THING: Record<AnswerKind, string> = {
  answer: "answer",
  props: "changes",
  merge: "choice",
};

/** The same, pointed at: "Stitch couldn’t take that answer." */
const ANSWER_THAT: Record<AnswerKind, string> = {
  answer: "that answer",
  props: "those changes",
  merge: "that choice",
};

function statusOf(error: unknown): number | null {
  if (typeof error !== "object" || error === null) return null;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" && Number.isFinite(status) ? status : null;
}

/**
 * The sentence a pause card shows when its answer did not go through. Every
 * card keeps what the family entered, so each message says so where it helps.
 *
 * - No status, or status 0 (offline, a dropped request, a thrown fetch): the
 *   answer never reached the studio.
 * - 5xx: the studio failed. The server's own wording is never shown, since it
 *   can carry internals.
 * - 401: the sign-in ran out. 403: the server only takes answers from the
 *   person who started the run. 404: the run is gone.
 * - 409 (other than NOT_WAITING, which sendAnswer never shows): the answer
 *   did not fit what the run is waiting for. An empty text asks for a few
 *   words; anything else (ANSWER_CONFLICT, a wrong kind, a missing props
 *   list) asks to try again, never in the server's wording.
 * - Any other 4xx: the API client's message, which is already a fixed,
 *   user-safe sentence (or the generic one when it has none).
 */
export function answerErrorMessage(error: unknown, kind: AnswerKind): string {
  const thing = ANSWER_THING[kind] ?? "answer";
  const status = statusOf(error);
  if (status === null || status === 0) {
    return `That didn’t reach the studio. Your ${thing} ${thing === "changes" ? "are" : "is"} still here: check your connection and send again.`;
  }
  if (status >= 500) {
    return `The studio couldn’t take your ${thing} just now. Nothing is lost: try again in a moment.`;
  }
  if (status === 401) return "You’ve been signed out. Sign in again in another tab, then send this again.";
  if (status === 403) return "Only the person who started this memory can answer the studio here.";
  if (status === 404) return `This run isn’t there any more, so your ${thing} couldn’t be saved.`;
  if (status === 409) {
    if (kind === "answer" && NEEDS_TEXT_PATTERN.test(detailOf(error))) return "Write a few words first.";
    return `Stitch couldn’t take ${ANSWER_THAT[kind] ?? "that answer"}. Please try again.`;
  }
  const message = error instanceof Error ? error.message.trim() : "";
  return message || `That didn’t go through. Try sending your ${thing} again.`;
}

/**
 * An Error carrying answerErrorMessage's sentence, with the original status
 * (if any) kept on `status` and the original error on `cause`.
 */
export function answerFailure(error: unknown, kind: AnswerKind): Error & { status?: number } {
  const out: Error & { status?: number } = new Error(answerErrorMessage(error, kind), { cause: error });
  const status = statusOf(error);
  if (status !== null) out.status = status;
  return out;
}

/**
 * Posts one answer for the run. Does nothing (resolves false) unless the run
 * is paused on this kind of input. A successful post records the answer and
 * dispatches `answered`, unless the run was swapped out meanwhile
 * (`currentBook` then returns null).
 *
 * A 409 NOT_WAITING (isNotWaiting) means the run has already moved past
 * this pause (it was answered before a reload, or in another tab): the pause
 * is closed with a neutral `answered` (summary null), recorded so a replay
 * closes it again, and the call resolves so the card simply goes away. The
 * server's wording is never logged. Any other failure, including every other
 * 409 (the answer did not fit the pause), rejects with answerFailure (a calm,
 * specific sentence for the card), dispatches nothing and records nothing, so
 * the card stays open with what the family entered.
 */
export async function sendAnswer(o: {
  answer: (body: AnswerRequest) => Promise<void>;
  body: AnswerRequest;
  pendingKind: AnswerKind | null;
  currentBook: () => RunBook | null;
  dispatch: (action: RunAction) => void;
  /** Called with the book once an answer is recorded in it, to save its answers. */
  onRecorded?: (book: RunBook) => void;
}): Promise<boolean> {
  if (o.pendingKind !== o.body.kind) return false;
  const sentAt = o.currentBook()?.folded ?? 0;
  let summary: string | null;
  try {
    await o.answer(o.body);
    summary = answerSummary(o.body);
  } catch (error) {
    if (!isNotWaiting(error)) throw answerFailure(error, o.body.kind);
    summary = null;
  }
  const book = o.currentBook();
  if (book) {
    o.dispatch(recordAnswer(book, o.body.kind, summary, sentAt));
    o.onRecorded?.(book);
  }
  return true;
}

export interface FollowRunOptions {
  /** Opens the run's event stream; must end quietly when the signal aborts. */
  stream: (signal: AbortSignal) => AsyncIterable<RunEventMessage>;
  book: RunBook;
  dispatch: (action: RunAction) => void;
  /** Aborting it stops the loop; nothing is dispatched afterwards. */
  signal: AbortSignal;
  /** The server sends the whole run again on every connection. */
  replayOnReconnect: boolean;
  maxRetries: number;
  retryDelayMs: (attempt: number) => number;
  /** Injected for tests. Defaults to an abortable setTimeout. */
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  /** Injected for tests. Defaults to Date.now. */
  now?: () => number;
  /** How long to wait for the stream to end after a final event (default FINISH_DRAIN_MS). */
  finishDrainMs?: number;
}

/**
 * Reads what is left of a stream after its final event, ignoring it, until
 * the stream ends (true) or `ms` pass or `signal` aborts first (false).
 */
async function drainStream(
  iterator: AsyncIterator<RunEventMessage>,
  ms: number,
  signal: AbortSignal,
): Promise<boolean> {
  let stop: () => void = () => {};
  const stopped = new Promise<"stop">((resolve) => {
    stop = () => resolve("stop");
  });
  const timer = setTimeout(stop, Math.max(0, ms));
  signal.addEventListener("abort", stop, { once: true });
  try {
    for (;;) {
      if (signal.aborted) return false;
      const step = await Promise.race([iterator.next(), stopped]);
      if (step === "stop") return false;
      if (step.done) return true;
      // Anything after the final event is not part of the run: drop it.
    }
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", stop);
  }
}

export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

/**
 * Reads the run's stream into `dispatch` until the run is over, reconnecting
 * after drops per nextConnectionStep. With replay, the first message of a
 * connection that follows earlier events resets the state, the replayed log
 * is folded again, and recorded answers are re-applied where they were given.
 * Resolves when finished, out of retries, or aborted.
 *
 * After a final event the rest of the stream is read (and ignored) until it
 * ends, for up to `finishDrainMs`; only a stream still open after that is
 * closed from this side. Aborting `signal` closes it at once.
 */
export async function followRun(o: FollowRunOptions): Promise<void> {
  const { book, dispatch, signal } = o;
  const sleep = o.sleep ?? abortableSleep;
  const now = o.now ?? Date.now;
  const drainMs = o.finishDrainMs ?? FINISH_DRAIN_MS;
  let failures = 0;

  while (!signal.aborted) {
    const highWaterBefore = book.highWater;
    const resetOnFirst = o.replayOnReconnect && book.folded > 0;
    const startedAt = now();
    let ended: ConnectionEnd = "dropped";

    // One controller per connection, so a stream left open is closed too.
    const conn = new AbortController();
    const forward = () => conn.abort();
    signal.addEventListener("abort", forward, { once: true });
    let iterator: AsyncIterator<RunEventMessage> | null = null;
    // Whether the stream may still be open when this connection is over.
    let open = true;
    try {
      let first = true;
      iterator = o.stream(conn.signal)[Symbol.asyncIterator]();
      for (;;) {
        const next = await iterator.next();
        if (next.done) {
          open = false;
          break;
        }
        const message = next.value;
        if (signal.aborted) break;
        if (first && resetOnFirst) {
          book.folded = 0;
          dispatch({ type: "reset" });
        }
        first = false;
        // Count before dispatching, so an answer recorded in reaction to this
        // event is placed after it.
        book.folded += 1;
        if (book.folded > book.highWater) book.highWater = book.folded;
        // Answers given earlier at this point of the run (an answer recorded
        // while this event is dispatched is dispatched by whoever records it).
        const due = book.answers.filter((a) => a.after === book.folded);
        dispatch({ type: "event", message });
        for (const a of due) dispatch({ type: "answered", kind: a.kind, summary: a.summary });
        if (isFinalEvent(message.event)) {
          ended = "finished";
          // Let the server end the stream itself; closing it first is an
          // aborted request in the browser.
          open = !(await drainStream(iterator, drainMs, signal));
          break;
        }
      }
    } catch {
      // A thrown stream is a drop; the policy below decides what follows.
      // It has ended, but its connection may not be closed yet.
    } finally {
      signal.removeEventListener("abort", forward);
      if (open || signal.aborted) {
        conn.abort();
        // Release the stream (as a `for await` break would); it ends quietly
        // once its signal aborts.
        void iterator?.return?.()?.catch(() => undefined);
      }
    }
    if (signal.aborted) ended = "aborted";

    const step = nextConnectionStep(
      {
        ended,
        failures,
        progressed: connectionMadeProgress({
          highWaterBefore,
          foldedAfter: book.folded,
          connectedMs: now() - startedAt,
        }),
      },
      o.maxRetries,
      o.retryDelayMs,
    );
    if (step.kind === "stop") return;
    if (step.kind === "give_up") {
      dispatch({ type: "connection_error", message: step.message });
      return;
    }
    failures = step.failures;
    // The feed says so quietly until the next event arrives.
    dispatch({ type: "reconnecting" });
    await sleep(step.delayMs, signal);
  }
}
