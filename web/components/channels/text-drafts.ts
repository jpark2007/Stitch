export type TextDraft = { id: string; text: string; createdAt: string };

export class TextDraftsError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(status === 401 ? "Sign in again to see your text drafts." : "Your text drafts could not be loaded. Please try again.");
    this.status = status;
  }
}

export function createTextDraftsClient(baseUrl: string, fetcher: typeof fetch = fetch) {
  return async (familyId: string, personId: string, signal: AbortSignal): Promise<TextDraft[]> => {
    const response = await fetcher(`${baseUrl.replace(/\/+$/, "")}/families/${encodeURIComponent(familyId)}/channels/people/${encodeURIComponent(personId)}/text-drafts`, {
      credentials: "include", cache: "no-store", signal,
    });
    if (!response.ok) throw new TextDraftsError(response.status);
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.drafts)) throw new Error("Your text drafts could not be loaded. Please try again.");
    return payload.drafts.map((value: unknown) => {
      if (!value || typeof value !== "object") throw new Error("Invalid text draft response.");
      const draft = value as Record<string, unknown>;
      if (typeof draft.id !== "string" || !draft.id || typeof draft.text !== "string" || typeof draft.createdAt !== "string" || !Number.isFinite(Date.parse(draft.createdAt))) {
        throw new Error("Invalid text draft response.");
      }
      return { id: draft.id, text: draft.text, createdAt: draft.createdAt };
    });
  };
}
