// Pure state for the live agent feed: folds the run's SSE events into one
// RunState. No runtime imports, and only erasable TypeScript, so
// `node --test` can load this file directly.
import type {
  CompileErrorEvent,
  FramesEvent,
  MergeEvent,
  PlanItem,
  PropChip,
  QuestionEvent,
  RunEvents,
} from "@/lib/types";

/** One SSE message from GET /families/{fid}/runs/{id}/stream. */
export type RunEventMessage = {
  [K in keyof RunEvents]: { event: K; data: RunEvents[K] };
}[keyof RunEvents];

export type RunPhase =
  | "connecting"
  | "running"
  | "waiting"
  | "scene_ready"
  | "done"
  | "error";

/** What the run is paused on. `kind` matches AnswerRequest.kind. */
export type PendingInput =
  | { kind: "answer"; question: QuestionEvent }
  | { kind: "props"; props: PropChip[] }
  | { kind: "merge"; merge: MergeEvent };

export interface FeedStep {
  key: string;
  name: string;
  task?: string | null;
  summary?: string | null;
  status: "running" | "done";
  /** Log sequence number when the step ended (null while it runs). Its span is (start entry, endSeq]. */
  endSeq?: number | null;
}

export type FeedEntry = { seq: number } & (
  | { kind: "step"; stepKey: string }
  | { kind: "compile_error"; event: CompileErrorEvent }
  | { kind: "frames"; event: FramesEvent }
  | { kind: "asked"; text: string }
  | {
      kind: "answered";
      /** Which pause this answers. */
      answer: PendingInput["kind"];
      /**
       * What the family chose (see answerSummary), or null when the pause was
       * answered somewhere this page did not see: the run moved on past it
       * (a reload after answering), or the server said it was no longer waiting.
       */
      summary: string | null;
      /** What was asked: the question or merge text; null for a props review. */
      prompt: string | null;
    }
  | { kind: "props"; count: number }
  | { kind: "merge"; text: string }
  | { kind: "scene_ready"; memoryId: string }
  | { kind: "error"; message: string }
);

export interface RunState {
  phase: RunPhase;
  todos: PlanItem[];
  steps: FeedStep[];
  log: FeedEntry[];
  pending: PendingInput | null;
  /**
   * Keys of the steps that were running when the current pause arrived, or
   * null when nothing is pending. The pause counts as answered once none of
   * them is running any more (see clearsPause).
   */
  pauseSteps: string[] | null;
  /** Log sequence number of the current pause's own entry, or null. Stable across replays. */
  pauseSeq: number | null;
  memoryId: string | null;
  version: number | null;
  error: string | null;
  /** Last sequence number handed to a log entry */
  seq: number;
}

export const initialRunState: RunState = {
  phase: "connecting",
  todos: [],
  steps: [],
  log: [],
  pending: null,
  pauseSteps: null,
  pauseSeq: null,
  memoryId: null,
  version: null,
  error: null,
  seq: 0,
};

export type RunAction =
  | { type: "event"; message: RunEventMessage }
  /** `summary` null: the pause was already answered elsewhere (logged as a neutral line). */
  | { type: "answered"; kind: PendingInput["kind"]; summary: string | null }
  | { type: "connection_error"; message: string }
  | { type: "reset" };

// Distributes over the FeedEntry union so each variant keeps its own fields.
type EntryBody = FeedEntry extends infer E
  ? E extends FeedEntry
    ? Omit<E, "seq">
    : never
  : never;

function withEntry(state: RunState, body: EntryBody): RunState {
  const seq = state.seq + 1;
  return { ...state, seq, log: [...state.log, { ...body, seq } as FeedEntry] };
}

/**
 * Phase and error after an informational event: wakes from connecting or
 * error, otherwise unchanged. Waking from error clears the stale message.
 */
function wake(state: RunState): Pick<RunState, "phase" | "error"> {
  if (state.phase === "connecting") return { phase: "running", error: state.error };
  if (state.phase === "error") return { phase: "running", error: null };
  return { phase: state.phase, error: state.error };
}

function promptOf(pending: PendingInput): string | null {
  if (pending.kind === "answer") return pending.question.text;
  if (pending.kind === "merge") return pending.merge.text;
  return null;
}

/** Closes the current pause (if any) and logs the answer line for it. */
function closePause(state: RunState, summary: string | null): RunState {
  const { pending } = state;
  if (!pending) return state;
  return withEntry(
    { ...state, pending: null, pauseSteps: null, pauseSeq: null },
    { kind: "answered", answer: pending.kind, summary, prompt: promptOf(pending) },
  );
}

function pause(state: RunState, pending: PendingInput, body: EntryBody): RunState {
  // The server shows one pause at a time, so a new one means the last was
  // answered (on another page, or before a reload): log that quietly first.
  const closed = closePause(state, null);
  const pauseSteps = closed.steps.filter((s) => s.status === "running").map((s) => s.key);
  return withEntry(
    { ...closed, pending, pauseSteps, pauseSeq: closed.seq + 1, phase: "waiting", error: null },
    body,
  );
}

/**
 * Whether `message` shows that the open pause has been answered, so a reload
 * (which replays the run from its first event, with no record of the answer)
 * does not bring back a card the family already answered. The run is paused
 * inside one of its tool calls; while it is, the root agent can neither start
 * another sub-agent nor finish the scene. So:
 *
 * - subagent_start: always (the root moved on).
 * - subagent_end: once none of the steps that were running at the pause still
 *   runs, or for any end when nothing was running at the pause (pauseOutlived,
 *   checked after the end is folded).
 * - scene_ready and done: always.
 *
 * plan, frames and compile_error never do: a sibling branch of the paused step
 * can send them while the run really waits.
 */
function clearsPause(state: RunState, message: RunEventMessage): boolean {
  if (!state.pending) return false;
  return message.event === "subagent_start" || message.event === "scene_ready" || message.event === "done";
}

/** After a subagent_end: true when the open pause has outlived every step running at the time. */
function pauseOutlived(state: RunState): boolean {
  if (!state.pending) return false;
  const watched = state.pauseSteps ?? [];
  if (watched.length === 0) return true;
  const running = new Set(state.steps.filter((s) => s.status === "running").map((s) => s.key));
  return !watched.some((key) => running.has(key));
}

function applyEvent(state: RunState, message: RunEventMessage): RunState {
  switch (message.event) {
    case "plan":
      return {
        ...state,
        ...wake(state),
        todos: message.data.todos.map((t) => ({ ...t })),
      };

    case "subagent_start": {
      const key = `step-${state.seq + 1}`;
      const step: FeedStep = {
        key,
        name: message.data.name,
        task: message.data.task ?? null,
        summary: null,
        status: "running",
        endSeq: null,
      };
      return withEntry(
        { ...state, ...wake(state), steps: [...state.steps, step] },
        { kind: "step", stepKey: key },
      );
    }

    case "subagent_end": {
      const { name, summary } = message.data;
      let index = -1;
      for (let i = state.steps.length - 1; i >= 0; i--) {
        const s = state.steps[i];
        if (s.name === name && s.status === "running") {
          index = i;
          break;
        }
      }
      const awake = wake(state);
      if (index >= 0) {
        const steps = state.steps.map((s, i) =>
          i === index
            ? { ...s, status: "done" as const, summary: summary ?? s.summary ?? null, endSeq: state.seq }
            : s,
        );
        return { ...state, ...awake, steps };
      }
      const key = `step-${state.seq + 1}`;
      const step: FeedStep = { key, name, task: null, summary: summary ?? null, status: "done", endSeq: state.seq + 1 };
      return withEntry(
        { ...state, ...awake, steps: [...state.steps, step] },
        { kind: "step", stepKey: key },
      );
    }

    case "question":
      return pause(
        state,
        { kind: "answer", question: { ...message.data } },
        { kind: "asked", text: message.data.text },
      );

    case "props_found":
      return pause(
        state,
        { kind: "props", props: message.data.props.map((p) => ({ ...p })) },
        { kind: "props", count: message.data.props.length },
      );

    case "merge":
      return pause(
        state,
        { kind: "merge", merge: { ...message.data } },
        { kind: "merge", text: message.data.text },
      );

    case "compile_error":
      return withEntry(
        { ...state, ...wake(state) },
        { kind: "compile_error", event: message.data },
      );

    case "frames":
      return withEntry(
        { ...state, ...wake(state) },
        { kind: "frames", event: message.data },
      );

    case "scene_ready":
      return withEntry(
        {
          ...state,
          phase: "scene_ready",
          memoryId: message.data.memoryId,
          version: message.data.version ?? null,
        },
        { kind: "scene_ready", memoryId: message.data.memoryId },
      );

    case "done":
      return {
        ...state,
        phase: "done",
        memoryId: message.data.memoryId,
        pending: null,
        pauseSteps: null,
        pauseSeq: null,
        error: null,
      };

    case "error":
      return withEntry(
        { ...state, phase: "error", error: message.data.message },
        { kind: "error", message: message.data.message },
      );

    default: {
      // Unknown event names from a newer server are ignored.
      return state;
    }
  }
}

export function runReducer(state: RunState, action: RunAction): RunState {
  if (action.type === "reset") return initialRunState;
  // A finished run is final: late events and the stream closing change nothing.
  if (state.phase === "done") return state;

  switch (action.type) {
    case "event": {
      const message = action.message;
      if (message.event === "subagent_end") {
        // Decided on the steps as they are after this one ends. A known
        // step's end logs nothing, so the quiet line still reads in order.
        const next = applyEvent(state, message);
        if (!pauseOutlived(next)) return next;
        return { ...closePause(next, null), phase: next.phase === "waiting" ? "running" : next.phase };
      }
      // A pause this event shows was answered closes first (with a quiet
      // line), so the log reads in order: the answer, then what came next.
      // Its phase becomes running here; wake() and the event take it on.
      const s = clearsPause(state, message) ? { ...closePause(state, null), phase: "running" as const } : state;
      return applyEvent(s, message);
    }

    case "answered": {
      if (!state.pending || state.pending.kind !== action.kind) {
        return action.summary === null ? state : fillNeutralAnswer(state, action.kind, action.summary);
      }
      return { ...closePause(state, action.summary), phase: "running", error: null };
    }

    case "connection_error":
      return withEntry(
        { ...state, phase: "error", error: action.message },
        { kind: "error", message: action.message },
      );

    default:
      return state;
  }
}

/**
 * The family's own answer, arriving after the pause was already closed with a
 * neutral line (the run moved on while the answer was in flight, or a replay
 * re-applies it after the event that closed the pause): it fills in that
 * line instead of logging the answer twice. Only the latest pause line, and
 * only while it is still neutral; otherwise nothing changes.
 */
function fillNeutralAnswer(state: RunState, kind: PendingInput["kind"], summary: string): RunState {
  for (let i = state.log.length - 1; i >= 0; i--) {
    const e = state.log[i];
    if (e.kind === "asked" || e.kind === "props" || e.kind === "merge") return state;
    if (e.kind !== "answered") continue;
    if (e.answer !== kind || e.summary !== null) return state;
    const log = state.log.slice();
    log[i] = { ...e, summary };
    return { ...state, log };
  }
  return state;
}

/** True while the run is paused on the family: show FollowUpCard, PropChips or the merge card. */
export function needsInput(s: RunState): boolean {
  return s.phase === "waiting" && s.pending !== null;
}
