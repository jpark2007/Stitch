// Pure helpers for the memory run panel (MemoryRun.tsx): which pause card to
// show, the key that remounts it for each new pause, and the two bits of the
// panel that depend on how far the run has got. No runtime imports, and only
// erasable TypeScript, so `node --test` can load this file directly.
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
 * carries no id, so the key is the kind plus the log sequence number: the
 * reducer logs every pause as it arrives, so `seq` is the pause's own entry
 * until something else is logged. MemoryRun reads it once per pending input.
 */
export function pendingKey(s: Pick<RunState, "pending" | "seq">): string {
  return s.pending === null ? "none" : `${s.pending.kind}-${s.seq}`;
}

/** The scene link for the feed: only once the run has named its memory. */
export function sceneHrefFor(
  s: Pick<RunState, "memoryId">,
  sceneHref?: (memoryId: string) => string,
): string | undefined {
  return s.memoryId && sceneHref ? sceneHref(s.memoryId) : undefined;
}

/** "Add another memory" shows only once the run is done, and only with somewhere to go. */
export function addAnotherHrefFor(
  s: Pick<RunState, "phase">,
  addAnotherHref?: string,
): string | undefined {
  return s.phase === "done" && addAnotherHref ? addAnotherHref : undefined;
}
