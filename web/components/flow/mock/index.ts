// Mock mode (NEXT_PUBLIC_API_MOCK=1): one in-memory backend per page load,
// seeded with the demo family and persisted to localStorage in the browser.
// The API client is wired to it as
//   createApiClient({ fetch: backend.fetch, mediaUrl: backend.mediaUrl })
// This is the only mock file with runtime imports; tests import the modules
// it composes directly.
import type { MemoryResponse } from "@/lib/types";
import { buildDemoSeed } from "./demoSeed";
import { createMockBackend } from "./mockBackend";
import type { MockBackend, MockFault } from "./mockBackend";

export type { MockBackend, MockBackendOptions, MockFault } from "./mockBackend";
export type { MockSeed } from "./demoSeed";
export { DEMO_INVITE_CODE, DEMO_PASSWORD } from "./demoSeed";

const SAMPLE_URL = "/samples/hymn_books.memory.json";

let backend: MockBackend | null = null;

async function loadSeed() {
  const res = await fetch(SAMPLE_URL);
  if (!res.ok) throw new Error(`Couldn't load ${SAMPLE_URL} (${res.status})`);
  const sample = (await res.json()) as MemoryResponse;
  return buildDemoSeed(sample);
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
    backend = createMockBackend({ seed: loadSeed, storage: browserStorage() });
    if (typeof window !== "undefined") {
      const w = window as unknown as SweepWindow;
      w.__stitchMock = backend;
      for (const fault of Array.isArray(w.__stitchMockFaults) ? w.__stitchMockFaults : []) backend.inject(fault);
    }
  }
  return backend;
}
