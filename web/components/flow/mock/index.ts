// Mock mode (NEXT_PUBLIC_API_MOCK=1): one in-memory backend per page load,
// seeded with the demo family and persisted to localStorage in the browser.
// The API client is wired to it as
//   createApiClient({ fetch: backend.fetch, mediaUrl: backend.mediaUrl })
// This is the only mock file with runtime imports; tests import the modules
// it composes directly.
import type { MemoryResponse } from "@/lib/types";
import { buildDemoSeed } from "./demoSeed";
import { createMockBackend } from "./mockBackend";
import type { MockBackend } from "./mockBackend";

export type { MockBackend, MockBackendOptions } from "./mockBackend";
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

/** The shared mock backend for this page load. */
export function getMockBackend(): MockBackend {
  if (!backend) {
    backend = createMockBackend({ seed: loadSeed, storage: browserStorage() });
  }
  return backend;
}
