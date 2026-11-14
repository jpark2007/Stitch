// Mock mode (NEXT_PUBLIC_API_MOCK=1): one in-memory backend per page load,
// seeded with the demo family and persisted to localStorage in the browser,
// runs included (a reload or a second tab picks a run up where it stopped).
// The API client is wired to it as
//   createApiClient({ fetch: backend.fetch, mediaUrl: backend.mediaUrl })
// This is the only mock file with runtime imports; tests import the modules
// it composes directly.
import type { MemoryResponse } from "@/lib/types";
import { buildDemoSeed } from "./demoSeed";
import { buildDemoTraditions } from "./demoTraditions";
import { createMockBackend, MOCK_RUNS_KEY, MOCK_STORAGE_KEY } from "./mockBackend";
import type { MockBackend, MockFault } from "./mockBackend";

export type { MockBackend, MockBackendOptions, MockFault } from "./mockBackend";
export type { MockSeed } from "./demoSeed";
export { DEMO_INVITE_CODE, DEMO_PASSWORD } from "./demoSeed";
export { DEMO_TRADITIONS } from "./demoTraditions";

const SAMPLE_URL = "/samples/hymn_books.memory.json";
/** How often a page load checks the runs it follows for a driver that went away. */
const RUN_CHECK_MS = 2000;

let backend: MockBackend | null = null;

async function loadSeed() {
  const res = await fetch(SAMPLE_URL);
  if (!res.ok) throw new Error(`Couldn't load ${SAMPLE_URL} (${res.status})`);
  const sample = (await res.json()) as MemoryResponse;
  const seed = buildDemoSeed(sample);
  return { ...seed, traditions: buildDemoTraditions(seed.families[0].id) };
}

function browserStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * For browser sweeps: the backend is `window.__stitchMock` (call
 * `inject({...})` on it to fail or slow a route), and faults listed in
 * `window.__stitchMockFaults` before the page loads (Playwright's
 * add_init_script) are armed as soon as it is created.
 */
interface SweepWindow {
  __stitchMock?: MockBackend;
  __stitchMockFaults?: MockFault[];
}

/** The shared mock backend for this page load. */
export function getMockBackend(): MockBackend {
  if (!backend) {
    const created = createMockBackend({ seed: loadSeed, storage: browserStorage() });
    backend = created;
    if (typeof window !== "undefined") {
      const w = window as unknown as SweepWindow;
      w.__stitchMock = created;
      for (const fault of Array.isArray(w.__stitchMockFaults) ? w.__stitchMockFaults : []) created.inject(fault);
      // Another tab wrote the state or a run (the event never fires in the
      // tab that wrote): catch up, so a run followed here keeps moving.
      window.addEventListener("storage", (e) => {
        if (e.key === null || e.key === MOCK_STORAGE_KEY || e.key === MOCK_RUNS_KEY) created.sync();
      });
      // A tab that closes or reloads lets go of the runs it drives, so a tab
      // still showing one carries it on (through the storage event above).
      window.addEventListener("pagehide", () => created.release());
      // Back from the back-forward cache: pick up what changed meanwhile.
      window.addEventListener("pageshow", (e) => {
        if (e.persisted) created.sync();
      });
      // A driver that vanished without letting go (a crashed tab) writes
      // nothing, so no storage event comes: look now and then.
      window.setInterval(() => created.checkRuns(), RUN_CHECK_MS);
    }
  }
  return backend;
}
