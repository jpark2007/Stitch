import { parseContact, parseTestPromptResult } from "./settings";
import type { ChannelContact, Enrollment, Preferences } from "./settings";

export class ChannelSettingsError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(status === 401 ? "Your session has ended. Sign in again to continue."
      : status === 403 ? "You no longer have permission to change this contact. Refresh your session."
      : status === 404 || status === 503 ? "Contact settings are unavailable here, or this family is no longer accessible."
      : status === 409 ? "This contact cannot be enrolled now. A pending code lasts 10 minutes; active contacts and changed account mappings cannot be enrolled again."
      : status === 422 ? "Please check the account, time zone and quiet hours."
      : status === 429 ? "Too many enrollment attempts. Please wait before requesting another code."
      : "The change could not be confirmed. Refresh the contact before trying again.");
    this.name = "ChannelSettingsError";
    this.status = status;
  }
}

export function createSettingsClient(baseUrl: string, fetcher: typeof fetch = fetch) {
  async function request(method: string, fid: string, personId: string, suffix: string, body?: unknown, signal?: AbortSignal) {
    let response: Response;
    try {
      response = await fetcher(`${baseUrl.replace(/\/+$/, "")}/families/${encodeURIComponent(fid)}/channels/people/${encodeURIComponent(personId)}/${suffix}`, {
        method, credentials: "include", cache: "no-store", signal,
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new ChannelSettingsError(0);
    }
    if (!response.ok) throw new ChannelSettingsError(response.status);
    try { return await response.json(); }
    catch { throw new ChannelSettingsError(0); }
  }
  function contactResult(data: unknown, personId: string): ChannelContact | null {
    try {
      if (!data || typeof data !== "object" || !("contact" in data)) throw new Error();
      if (data.contact === null) return null;
      const c = parseContact(data.contact);
      if (c.personId !== personId) throw new Error();
      return c;
    } catch { throw new ChannelSettingsError(0); }
  }
  return {
    async testPrompt(fid: string, pid: string, requestId: string, signal?: AbortSignal) {
      const data = await request("POST", fid, pid, "contact/test-prompt", { requestId }, signal);
      try { return parseTestPromptResult(data); }
      catch { throw new ChannelSettingsError(0); }
    },
    async get(fid: string, pid: string, signal?: AbortSignal) {
      return contactResult(await request("GET", fid, pid, "contact", undefined, signal), pid);
    },
    async enroll(fid: string, pid: string, handle: string, prefs: Preferences, signal?: AbortSignal): Promise<Enrollment> {
      const data = await request("POST", fid, pid, "enrollment", { provider: "imessage", handle, ...prefs }, signal);
      const contact = contactResult(data, pid);
      if (!contact || contact.status !== "pending" || typeof data.code !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(data.code)
          || typeof data.expiresAt !== "string" || !Number.isFinite(Date.parse(data.expiresAt))
          || typeof data.instruction !== "string" || !data.instruction || data.instruction.length > 2000) throw new ChannelSettingsError(0);
      return { contact, code: data.code, expiresAt: data.expiresAt, instruction: data.instruction };
    },
    async save(fid: string, pid: string, prefs: Preferences, signal?: AbortSignal) {
      const c = contactResult(await request("PATCH", fid, pid, "contact", prefs, signal), pid);
      if (!c) throw new ChannelSettingsError(0);
      return c;
    },
    async stop(fid: string, pid: string, signal?: AbortSignal) {
      const c = contactResult(await request("POST", fid, pid, "contact/stop", {}, signal), pid);
      if (!c || c.status !== "stopped") throw new ChannelSettingsError(0);
      return c;
    },
  };
}
