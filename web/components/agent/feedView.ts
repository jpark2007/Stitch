// Pure decisions for the agent feed (AgentFeed.tsx): which log lines show,
// and the words the family reads. No runtime imports, and only erasable
// TypeScript, so `node --test` can load this file directly.
//
// The feed should read like a calm note from the studio, never like a build
// log (DESIGN.md "The feel"): one progress list, no line repeating what a card
// or another line already says, and nothing technical (compile errors, codes)
// in front of the family.
import type { CompileErrorEvent } from "@/lib/types";
import type { FeedEntry, FeedStep, PendingInput, RunState } from "./runState";

/** What the run adds: a new memory, or a second telling of a moment. */
export type FeedSubject = "memory" | "telling";

/** Without a plan, how many of the latest steps stay visible before the rest fold away. */
export const VISIBLE_STEPS = 4;

// ---------------------------------------------------------------------------
// Words

/** One friendly line for a compile error. Never the errors' messages or codes. */
export function compileNote(event: Pick<CompileErrorEvent, "round">): string {
  const round = event.round;
  const suffix = typeof round === "number" && Number.isFinite(round) ? ` (round ${round})` : "";
  return `The director is tidying up a few things${suffix}.`;
}

const PAUSE_LINE: Record<PendingInput["kind"], string> = {
  answer: "The studio has a question for you.",
  props: "Have a look at the things in your story.",
  merge: "The studio wants to check someone with you.",
};

/** The line under the heading; null when finished (the heading says it all). */
export function phaseLine(state: Pick<RunState, "phase" | "pending">): string | null {
  switch (state.phase) {
    case "connecting":
      return "Opening the studio…";
    case "waiting":
      return state.pending ? PAUSE_LINE[state.pending.kind] : "The studio is working on your moment.";
    case "scene_ready":
      return "The studio is adding a few last touches.";
    case "done":
      return null;
    case "error":
      return "Something went wrong.";
    default:
      return "The studio is working on your moment.";
  }
}

/** The heading once the run is done. */
export function finishedHeading(subject: FeedSubject = "memory"): string {
  return subject === "telling" ? "Your telling is stitched in" : "Your memory is stitched in";
}

/** The one sentence under the finished heading. */
export function finishedNote(state: Pick<RunState, "memoryId">): string {
  return state.memoryId
    ? "Your page is ready, and it’s in everyone’s books."
    : "All done. Thank you for sharing this one.";
}

/** The log line for a pause, while it is still open in the log (the card is not showing). */
export function pauseLine(entry: FeedEntry): string | null {
  switch (entry.kind) {
    case "asked":
      return entry.text;
    case "props":
      return `Found ${entry.count} ${entry.count === 1 ? "prop" : "props"} for you to look over.`;
    case "merge":
      return entry.text;
    default:
      return null;
  }
}

/**
 * The one line for a pause and its answer: `prompt` is what was asked (shown
 * softly first; null for a props review), `text` is what the family chose.
 * `summary` is answerSummary's wording (runConnection.ts); null means the
 * pause was answered somewhere this page did not see.
 */
export function answeredLine(entry: Extract<FeedEntry, { kind: "answered" }>): {
  prompt: string | null;
  text: string;
} {
  const { summary } = entry;
  switch (entry.answer) {
    case "answer":
      return { prompt: entry.prompt, text: summary === null ? "Answered earlier." : `You answered: “${summary}”` };
    case "props":
      if (summary === null) return { prompt: null, text: "You looked over the props earlier." };
      if (summary === "No changes") return { prompt: null, text: "You looked over the props and kept them as they were." };
      return { prompt: null, text: `You looked over the props: ${propsPhrase(summary)}` };
    case "merge":
      if (summary === null) return { prompt: entry.prompt, text: "Answered earlier." };
      // answerSummary says "Yes, the same person" or "No, different people".
      return {
        prompt: entry.prompt,
        text: /^yes\b/i.test(summary) ? "You said they’re the same person." : "You said they’re different people.",
      };
    default:
      return { prompt: null, text: summary ?? "Answered earlier." };
  }
}

/** "Kept 2 props, renamed 1" -> "kept 2, renamed 1": the sentence already names the props. */
function propsPhrase(summary: string): string {
  const line = summary.replace(/^(\S+ \d+) props?\b/, "$1");
  return line.charAt(0).toLowerCase() + line.slice(1);
}

/**
 * The detail under a step's name: its task while it runs, its summary once
 * done. A finished run closes every step.
 *
 * No repeated fact: a step during which the family answered a pause (its
 * answer line lies inside the step's span in the log, from the step's own
 * entry to where it ended) shows its task, not its summary. The sub-agent's
 * summary of such a step restates what the family just chose (the prop
 * master's "Kept 2 props." right after "You looked over the props: kept 2"),
 * and the answer line already says it. A pause closed without the family's
 * words on this page ("Answered earlier.") keeps the summary, since then
 * nothing else says it.
 */
export function stepDetail(state: Pick<RunState, "log" | "phase" | "seq">, step: FeedStep): string | null {
  const done = step.status === "done" || state.phase === "done";
  if (!done) return step.task ?? null;
  if (step.summary && !answeredDuring(state, step)) return step.summary;
  return step.task ?? step.summary ?? null;
}

function answeredDuring(state: Pick<RunState, "log" | "seq">, step: FeedStep): boolean {
  const start = state.log.find((e) => e.kind === "step" && e.stepKey === step.key)?.seq;
  if (start === undefined) return false;
  const end = step.endSeq ?? state.seq;
  return state.log.some((e) => e.kind === "answered" && e.summary !== null && e.seq > start && e.seq <= end);
}

// ---------------------------------------------------------------------------
// Which lines show

type LogState = Pick<RunState, "log" | "phase" | "pending" | "pauseSeq" | "todos" | "steps">;

/** Sequence numbers of pause entries (asked / props / merge) that have their answer line. */
function answeredPauses(log: FeedEntry[]): Set<number> {
  const out = new Set<number>();
  let open: number | null = null;
  for (const e of log) {
    if (e.kind === "asked" || e.kind === "props" || e.kind === "merge") open = e.seq;
    else if (e.kind === "answered" && open !== null) {
      out.add(open);
      open = null;
    }
  }
  return out;
}

/**
 * Lines that never show:
 * - a pause's own line once answered: its answer line tells the exchange;
 * - the open pause's own line while its card is up: the card says it;
 * - "the scene is ready" once the page is ready or done: the panel below says it;
 * - the latest error while failed: the error box repeats it.
 */
function neverShown(state: LogState, answered: Set<number>): (e: FeedEntry) => boolean {
  const last = state.log.at(-1);
  const cardOpen = state.phase === "waiting" && state.pending !== null;
  return (e) => {
    switch (e.kind) {
      case "asked":
      case "props":
      case "merge":
        return answered.has(e.seq) || (cardOpen && e.seq === state.pauseSeq);
      case "scene_ready":
        return state.phase === "scene_ready" || state.phase === "done";
      case "error":
        return state.phase === "error" && e === last;
      default:
        return false;
    }
  };
}

/**
 * Lines folded behind "Show all steps":
 * - once done, everything: the finished panel is the news;
 * - with a plan, the plan is the progress, so sub-agent step rows and
 *   frames without a critique fold away; pause and answer lines, frames with
 *   a critique, errors and the compile note stay;
 * - without a plan, finished steps older than the latest VISIBLE_STEPS.
 */
function folded(state: LogState): (e: FeedEntry) => boolean {
  if (state.phase === "done") return () => true;
  if (state.todos.length > 0) {
    return (e) => e.kind === "step" || (e.kind === "frames" && !e.event.critique);
  }
  const stepEntries = state.log.filter((e) => e.kind === "step");
  const recent = new Set(stepEntries.slice(-VISIBLE_STEPS).map((e) => e.stepKey));
  const status = new Map(state.steps.map((s) => [s.key, s.status]));
  return (e) => e.kind === "step" && !recent.has(e.stepKey) && status.get(e.stepKey) === "done";
}

/** The log lines to render, in order. `showAll` opens what is folded. */
export function visibleEntries(state: LogState, showAll: boolean): FeedEntry[] {
  const hide = neverShown(state, answeredPauses(state.log));
  const fold = folded(state);
  return state.log.filter((e) => !hide(e) && (showAll || !fold(e)));
}

/** How many lines "Show all steps" would add. */
export function hiddenEntryCount(state: LogState): number {
  const hide = neverShown(state, answeredPauses(state.log));
  const fold = folded(state);
  return state.log.filter((e) => !hide(e) && fold(e)).length;
}

/** The disclosure's label. */
export function toggleLabel(state: Pick<RunState, "phase" | "todos">, showAll: boolean, hidden: number): string {
  if (showAll) return state.phase === "done" || state.todos.length > 0 ? "Hide the steps" : "Hide earlier steps";
  if (state.phase === "done") return "Show all steps";
  if (state.todos.length > 0) return `Show all steps (${hidden})`;
  return `Show all steps (${hidden} earlier)`;
}
