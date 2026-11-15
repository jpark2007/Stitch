export type CallPairing = { code: string; expiresAt: string; personId: string; instruction: string; phoneNumber: string | null };
export type CallDraft = { id: string; personId: string; createdAt: string; text: string };

export class CallSettingsError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(status === 401 ? "Your session has ended. Sign in again to review your calls."
      : status === 404 || status === 403 ? "Call interviews are unavailable here, or your access has changed."
      : status === 429 ? "Please wait before requesting another call code."
      : "We could not confirm that request. Refresh your drafts before trying again.");
    this.status = status;
  }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new CallSettingsError(0);
  return value as Record<string, unknown>;
}
function date(value: unknown): value is string { return typeof value === "string" && Number.isFinite(Date.parse(value)); }
function draft(value: unknown, pid: string): CallDraft {
  const d = object(value);
  if (typeof d.id !== "string" || !d.id || d.personId !== pid || !date(d.createdAt) || typeof d.text !== "string") throw new CallSettingsError(0);
  return { id: d.id, personId: pid, createdAt: d.createdAt, text: d.text };
}

export function createCallsClient(baseUrl: string, fetcher: typeof fetch = fetch) {
  async function request(fid: string, path: string, method: string, signal?: AbortSignal) {
    let response: Response;
    try {
      response = await fetcher(`${baseUrl.replace(/\/+$/, "")}/families/${encodeURIComponent(fid)}/channels/${path}`, {
        method, credentials: "include", cache: "no-store", signal,
        ...(method === "POST" ? { headers: { "Content-Type": "application/json" }, body: "{}" } : {}),
      });
    } catch (e) { if (signal?.aborted) throw e; throw new CallSettingsError(0); }
    if (!response.ok) throw new CallSettingsError(response.status);
    if (method === "DELETE") return null;
    try { return object(await response.json()); } catch { throw new CallSettingsError(0); }
  }
  return {
    async pair(fid: string, pid: string, signal?: AbortSignal): Promise<CallPairing> {
      const d = (await request(fid, `people/${encodeURIComponent(pid)}/call-pairing`, "POST", signal))!;
      if (typeof d.code !== "string" || !/^\d{4}-\d{4}-\d{4}$/.test(d.code) || d.personId !== pid || !date(d.expiresAt)
          || typeof d.instruction !== "string" || !(d.phoneNumber === null || (typeof d.phoneNumber === "string" && /^\+[1-9]\d{7,14}$/.test(d.phoneNumber)))) throw new CallSettingsError(0);
      return { code: d.code, expiresAt: d.expiresAt, personId: pid, instruction: d.instruction, phoneNumber: d.phoneNumber };
    },
    async list(fid: string, pid: string, signal?: AbortSignal): Promise<CallDraft[]> {
      const d = (await request(fid, "call-drafts", "GET", signal))!;
      if (!Array.isArray(d.drafts)) throw new CallSettingsError(0);
      return d.drafts.map(value => draft(value, pid));
    },
    async get(fid: string, pid: string, id: string, signal?: AbortSignal): Promise<CallDraft> {
      const d = draft((await request(fid, `call-drafts/${encodeURIComponent(id)}`, "GET", signal))!.draft, pid);
      if (d.id !== id) throw new CallSettingsError(0);
      return d;
    },
    async remove(fid: string, id: string, signal?: AbortSignal) { await request(fid, `call-drafts/${encodeURIComponent(id)}`, "DELETE", signal); },
  };
}
