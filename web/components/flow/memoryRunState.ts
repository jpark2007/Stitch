// Pure helpers for the memory run panel (MemoryRun.tsx): which pause card to
// show, the key that remounts it for each new pause, and the two bits of the
// panel that depend on how far the run has got. No runtime imports, and only
// erasable TypeScript, so `node --test` can load this file directly.
// Named apart from MemoryRun.tsx on purpose: on a case-insensitive disk
// (macOS) "memoryRun.ts" would shadow "MemoryRun.tsx" for every importer.
import type { RunState } from "@/components/agent/runState";

/** The card the run is paused on. Matches PendingInput.kind and AnswerRequest.kind. */
export type PendingCard = "answer" | "props" | "merge";

/**
 * The card to show: the pending input's kind while the run is paused on the
 * family, otherwise null. A pending input left over in another phase (the
 * connection dropped mid-pause, say) shows no card: the run is not listening.
 */
export function pendingCard(s: Pick<RunState, "phase" | "pending">): PendingCard | null {
  if (s.phase !== "waiting" || s.pending === null) return null;
  return s.pending.kind;
}

/**
 * A key that is new for every pause, so the card remounts with fresh state
 * between two follow-up questions (even two with the same text). QuestionEvent
 * carries no id, so the key is the kind plus the sequence number of the
 * pause's own log entry (`pauseSeq`, which the reducer sets with every pause
 * and clears when the pause closes, answered or not). It stays the same while
 * other lines are logged mid-pause and when a reconnect replays the run, so
 * the card is not remounted and what the family typed survives. A state
 * without `pauseSeq` falls back to `seq`, the pause's entry until something
 * else is logged. MemoryRun reads it once per pending input.
 */
export function pendingKey(s: Pick<RunState, "pending" | "seq"> & Partial<Pick<RunState, "pauseSeq">>): string {
  return s.pending === null ? "none" : `${s.pending.kind}-${s.pauseSeq ?? s.seq}`;
}

/** The scene link for the feed: only once the run has named its memory. */
export function sceneHrefFor(
  s: Pick<RunState, "memoryId">,
  sceneHref?: (memoryId: string) => string,
): string | undefined {
  return s.memoryId && sceneHref ? sceneHref(s.memoryId) : undefined;
}

/**
 * The quiet "watch the scene" link: only once the run is done and has named
 * its memory, and only with a builder (none in mock mode, where the stage
 * cannot load the moment).
 */
export function watchHrefFor(
  s: Pick<RunState, "memoryId" | "phase">,
  watchHref?: (memoryId: string) => string,
): string | undefined {
  return s.phase === "done" && s.memoryId && watchHref ? watchHref(s.memoryId) : undefined;
}

/** "Add another memory" shows only once the run is done, and only with somewhere to go. */
export function addAnotherHrefFor(
  s: Pick<RunState, "phase">,
  addAnotherHref?: string,
): string | undefined {
  return s.phase === "done" && addAnotherHref ? addAnotherHref : undefined;
}

/**
 * "Tell it again" shows only when the run ended with its own error: replaying
 * the run (all a reconnect can do) brings that error straight back, so the way
 * on is to send the story again. A connection that gave up keeps the feed's
 * Retry instead. Returns `action` then, otherwise undefined.
 */
export function tellAgainFor<T>(s: Pick<RunState, "phase" | "errorFrom">, action?: T): T | undefined {
  return s.phase === "error" && s.errorFrom === "run" ? action : undefined;
}
