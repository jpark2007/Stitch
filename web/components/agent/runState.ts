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
}

export type FeedEntry = { seq: number } & (
  | { kind: "step"; stepKey: string }
  | { kind: "compile_error"; event: CompileErrorEvent }
  | { kind: "frames"; event: FramesEvent }
  | { kind: "asked"; text: string }
  | { kind: "answered"; summary: string }
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
  memoryId: null,
  version: null,
  error: null,
  seq: 0,
};

export type RunAction =
  | { type: "event"; message: RunEventMessage }
  | { type: "answered"; kind: PendingInput["kind"]; summary: string }
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

/** Phase after an informational event: wakes from connecting or error, otherwise unchanged. */
function activePhase(phase: RunPhase): RunPhase {
  return phase === "connecting" || phase === "error" ? "running" : phase;
}

function pause(state: RunState, pending: PendingInput, body: EntryBody): RunState {
  return withEntry({ ...state, pending, phase: "waiting" }, body);
}

function applyEvent(state: RunState, message: RunEventMessage): RunState {
  switch (message.event) {
    case "plan":
      return {
        ...state,
        phase: activePhase(state.phase),
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
      };
      return withEntry(
        { ...state, phase: activePhase(state.phase), steps: [...state.steps, step] },
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
      const phase = activePhase(state.phase);
      if (index >= 0) {
        const steps = state.steps.map((s, i) =>
          i === index ? { ...s, status: "done" as const, summary: summary ?? s.summary ?? null } : s,
        );
        return { ...state, phase, steps };
      }
      const key = `step-${state.seq + 1}`;
      const step: FeedStep = { key, name, task: null, summary: summary ?? null, status: "done" };
      return withEntry(
        { ...state, phase, steps: [...state.steps, step] },
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
        { ...state, phase: activePhase(state.phase) },
        { kind: "compile_error", event: message.data },
      );

    case "frames":
      return withEntry(
        { ...state, phase: activePhase(state.phase) },
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
    case "event":
      return applyEvent(state, action.message);

    case "answered":
      if (!state.pending || state.pending.kind !== action.kind) return state;
      return withEntry(
        { ...state, pending: null, phase: "running" },
        { kind: "answered", summary: action.summary },
      );

    case "connection_error":
      return withEntry(
        { ...state, phase: "error", error: action.message },
        { kind: "error", message: action.message },
      );

    default:
      return state;
  }
}

/** True while the run is paused on the family: show FollowUpCard, PropChips or the merge card. */
export function needsInput(s: RunState): boolean {
  return s.phase === "waiting" && s.pending !== null;
}
