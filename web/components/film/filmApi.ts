// The API client for the film pages. Against the real API it is the session's
// own client. In mock mode (NEXT_PUBLIC_API_MOCK=1) the shared client in
// components/flow/client.ts does not answer the film routes yet, so this
// wraps the same mock backend with lib/api.ts withMockFilms: the film routes
// are answered there (the sample film, and films made on this page load) and
// everything else (sign-in, photo uploads, media URLs) goes to the mock
// backend as before. Once client.ts wires withMockFilms itself, the film
// pages can use the session's client directly.
import { getMockBackend } from "@/components/flow/mock";
import { createApiClient, isMockMode, withMockFilms, type StitchApi } from "@/lib/api";

let mockClient: StitchApi | null = null;

/** The client the film pages talk to: `api` itself, or in mock mode one that also answers the film routes. */
export function filmApi(api: StitchApi): StitchApi {
  if (!isMockMode()) return api;
  if (!mockClient) {
    const backend = getMockBackend();
    mockClient = createApiClient({ fetch: withMockFilms(backend.fetch), mediaUrl: backend.mediaUrl });
  }
  return mockClient;
}
