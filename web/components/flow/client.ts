// The API client the flow pages use, and the glue between it and the agent
// run hook. In mock mode (NEXT_PUBLIC_API_MOCK=1) the client talks to the
// in-memory mock backend instead of the server; everything else is the same.

import type { RunSource, UseAgentRunOptions } from "@/components/agent/useAgentRun";
import { api, createApiClient, isMockMode, withMockFilms, type StitchApi } from "@/lib/api";
import { DEMO_PASSWORD, getMockBackend } from "./mock";

let client: StitchApi | null = null;

/** The client for this page load: the real `api`, or one wired to the mock backend. */
export function getApi(): StitchApi {
  if (!client) {
    if (isMockMode()) {
      const backend = getMockBackend();
      client = createApiClient({ fetch: withMockFilms(backend.fetch), mediaUrl: backend.mediaUrl });
    } else {
      client = api;
    }
  }
  return client;
}

/** A useAgentRun source for one run of family `fid`. */
export function makeRunSource(client: StitchApi, fid: string, runId: string): RunSource {
  return {
    stream: (signal) => client.streamRun(fid, runId, { signal }),
    answer: (body) => client.answerRun(fid, runId, body),
  };
}

/**
 * Options for useAgentRun, the same for both backends.
 *
 * replayOnReconnect is true because every backend sends a subscriber the whole
 * run from its first event on each connection, and none reads Last-Event-ID:
 * - Real agent: RunManager.stream_run in api/agent/runs.py starts every
 *   subscriber at index 0 of run.events ("each gets the full sequence"), and
 *   the stream route in api/routes/memories.py passes it through unchanged.
 * - Scripted runs (STITCH_AGENT=fake, api/seed/fake_runs.py): stream_run gives
 *   "every event from the first, then live ones".
 * - Mock (components/flow/mock/mockBackend.ts): streamResponse starts its
 *   cursor at 0 for every request.
 * So the hook resets its feed on the first event of a reconnect rather than
 * folding the replayed events in twice.
 */
export const RUN_OPTIONS: UseAgentRunOptions = { replayOnReconnect: true };

/** The seeded demo account to prefill sign-in with in mock mode; null against the real API. */
export function demoCredentials(): { email: string; password: string } | null {
  if (!isMockMode()) return null;
  // David, the admin of the demo family in mock/demoSeed.ts.
  return { email: "david@example.com", password: DEMO_PASSWORD };
}
