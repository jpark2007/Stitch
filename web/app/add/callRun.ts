// A phone call that starts its own memory (the demo host's automatic setting):
// while the plain form is on screen, ask the API now and then whether a call
// has started a run for this person, and open it. When the setting is off, or
// Channels is not mounted at all, the first answer says so and asking stops.
import { useEffect, useRef } from "react";

import { API_BASE_URL, isMockMode } from "@/lib/api";

import { sessionStore } from "./parts";

export const CALL_RUN_POLL_MS = 2500;
/** A run a call started this recently is opened even if it began before the page did. */
export const CALL_RUN_FRESH_MS = 120_000;
const SEEN_KEY = "stitch.add.callRuns";

export type CallRunAnswer = { automatic: boolean; run: { runId: string; createdAt: string } | null };

/** Reads the route's answer; anything unexpected is null (stop asking). */
export function readCallRun(value: unknown): CallRunAnswer | null {
  if (typeof value !== "object" || value === null) return null;
  const { automatic, run } = value as { automatic?: unknown; run?: unknown };
  if (typeof automatic !== "boolean") return null;
  if (run === null) return { automatic, run: null };
  if (typeof run !== "object" || run === undefined) return null;
  const { runId, createdAt } = run as { runId?: unknown; createdAt?: unknown };
  if (typeof runId !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(runId)) return null;
  if (typeof createdAt !== "string" || Number.isNaN(Date.parse(createdAt))) return null;
  return { automatic, run: { runId, createdAt } };
}

/**
 * The run to open, or null. `baseline` is the run already there when the page
 * first asked (undefined before the first answer): it opens only if it is
 * fresh. Any other run not opened before opens.
 */
export function callRunToOpen(
  answer: CallRunAnswer,
  baseline: string | null | undefined,
  seen: ReadonlySet<string>,
  now: number,
): string | null {
  const run = answer.run;
  if (!run || seen.has(run.runId)) return null;
  const first = baseline === undefined;
  if (first || run.runId === baseline) {
    return now - Date.parse(run.createdAt) <= CALL_RUN_FRESH_MS ? run.runId : null;
  }
  return run.runId;
}

function readSeen(): Set<string> {
  try {
    const raw: unknown = JSON.parse(sessionStore()?.getItem(SEEN_KEY) ?? "[]");
    return new Set(Array.isArray(raw) ? raw.filter((id): id is string => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

function markSeen(runId: string): void {
  try {
    sessionStore()?.setItem(SEEN_KEY, JSON.stringify([...readSeen(), runId].slice(-50)));
  } catch {
    // Blocked storage: the run may open twice in this tab, nothing worse.
  }
}

/** While `familyId` is set, opens each run a call starts for the signed-in person, once. */
export function useCallRun(familyId: string | null, onRun: (runId: string) => void): void {
  const open = useRef(onRun);
  useEffect(() => {
    open.current = onRun;
  }, [onRun]);
  useEffect(() => {
    if (!familyId || isMockMode()) return;
    const stop = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let baseline: string | null | undefined;
    const url = `${API_BASE_URL.replace(/\/+$/, "")}/families/${encodeURIComponent(familyId)}/channels/call-runs/latest`;
    const ask = async () => {
      let answer: CallRunAnswer | null = null;
      try {
        const response = await fetch(url, { credentials: "include", cache: "no-store", signal: stop.signal });
        // 404 (Channels off), 401, 503: this page has nothing to wait for.
        if (!response.ok) return;
        answer = readCallRun(await response.json());
      } catch {
        if (stop.signal.aborted) return;
        // The network blinked: ask again.
        timer = setTimeout(ask, CALL_RUN_POLL_MS * 2);
        return;
      }
      if (stop.signal.aborted || !answer || !answer.automatic) return;
      const runId = callRunToOpen(answer, baseline, readSeen(), Date.now());
      if (baseline === undefined) baseline = answer.run?.runId ?? null;
      if (runId) {
        markSeen(runId);
        open.current(runId);
        return;
      }
      timer = setTimeout(ask, CALL_RUN_POLL_MS);
    };
    void ask();
    return () => {
      stop.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [familyId]);
}
