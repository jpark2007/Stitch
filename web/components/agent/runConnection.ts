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

/** The line logged as "You answered: …" after an answer is accepted. */
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
  summary: string;
}

/** Book-keeping for one run, kept across reconnects and retries of the same source. */
export interface RunBook {
  /** Run events folded into the current state since its last reset. */
  folded: number;
  /** Most run events ever folded for this run. */
  highWater: number;
  answers: AnsweredRecord[];
}

export function newRunBook(): RunBook {
  return { folded: 0, highWater: 0, answers: [] };
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
  summary: string,
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
 * Posts one answer for the run. Does nothing (resolves false) unless the run
 * is paused on this kind of input. A failed post rejects with the source's
 * error and dispatches nothing; a successful one records the answer and
 * dispatches `answered`, unless the run was swapped out meanwhile
 * (`currentBook` then returns null).
 */
export async function sendAnswer(o: {
  answer: (body: AnswerRequest) => Promise<void>;
  body: AnswerRequest;
  pendingKind: AnswerKind | null;
  currentBook: () => RunBook | null;
  dispatch: (action: RunAction) => void;
}): Promise<boolean> {
  if (o.pendingKind !== o.body.kind) return false;
  const sentAt = o.currentBook()?.folded ?? 0;
  await o.answer(o.body);
  const book = o.currentBook();
  if (book) o.dispatch(recordAnswer(book, o.body.kind, answerSummary(o.body), sentAt));
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
 */
export async function followRun(o: FollowRunOptions): Promise<void> {
  const { book, dispatch, signal } = o;
  const sleep = o.sleep ?? abortableSleep;
  const now = o.now ?? Date.now;
  let failures = 0;

  while (!signal.aborted) {
    const highWaterBefore = book.highWater;
    const resetOnFirst = o.replayOnReconnect && book.folded > 0;
    const startedAt = now();
    let ended: ConnectionEnd = "dropped";

    // One controller per connection, so a finished stream is closed too.
    const conn = new AbortController();
    const forward = () => conn.abort();
    signal.addEventListener("abort", forward, { once: true });
    try {
      let first = true;
      for await (const message of o.stream(conn.signal)) {
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
          break;
        }
      }
    } catch {
      // A thrown stream is a drop; the policy below decides what follows.
    } finally {
      signal.removeEventListener("abort", forward);
      conn.abort();
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
    await sleep(step.delayMs, signal);
  }
}
