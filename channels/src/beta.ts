import { Webhook } from "standardwebhooks";
import { homedir } from "node:os";
import { join } from "node:path";
import { createServer, type Server } from "node:http";
import { ApiClient } from "./bridge.ts";
import { BetaStore, digest } from "./beta-store.ts";
import { downloadMedia } from "./media.ts";
import { PHOTO_TYPES, MAX_PHOTO_BYTES, ProtocolError, linkCode, parseMessage, parsePhoto, type Outbound } from "./protocol.ts";

type Obj = Record<string, any>;
const fail = (message = "Invalid Photon Beta event"): never => { throw new ProtocolError(422, message); };
const transient = (error: unknown): boolean => error instanceof ProtocolError
  ? error.status === 404 || error.status === 408 || error.status === 429 || error.status >= 500
  : error instanceof DOMException && ["AbortError", "TimeoutError"].includes(error.name);
function object(value: unknown): Obj { if (!value || typeof value !== "object" || Array.isArray(value)) fail(); return value as Obj; }
function id(value: unknown, prefix: string): string { if (typeof value !== "string" || !new RegExp(`^pho_${prefix}_[0-7][0-9a-hjkmnp-tv-z]{25}$`).test(value)) return fail(); return value; }
function bound(value: unknown, max = 256): string { if (typeof value !== "string" || !value.trim() || Buffer.byteLength(value) > max || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) return fail(); return value; }
async function boundedOperation<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  let rejectAbort: () => void;
  const aborted = new Promise<never>((_, reject) => { rejectAbort = () => reject(new ProtocolError(504, "Photon request timed out")); signal.addEventListener("abort", rejectAbort, { once: true }); if (signal.aborted) rejectAbort(); });
  try { return await Promise.race([operation, aborted]); }
  catch (error) { if (error instanceof TypeError) throw new ProtocolError(502, "Photon transport failed"); throw error; }
  finally { signal.removeEventListener("abort", rejectAbort!); }
}
export type BetaConfig = { projectId: string; apiKey: string; webhookSecret: string; agentHandle: string; statePath: string; webhookPort: number };
export function betaConfig(env: NodeJS.ProcessEnv): BetaConfig {
  if (env.CHANNELS_PHOTON_VERSION !== "beta") throw new Error("Cloud mode requires CHANNELS_PHOTON_VERSION=beta; Stable cloud is disabled");
  const projectId = id(env.PHOTON_PROJECT_ID, "prj");
  const apiKey = env.PHOTON_API_KEY ?? "", webhookSecret = env.PHOTON_WEBHOOK_SECRET ?? "";
  if (!/^pho_sk_[A-Za-z0-9_-]+$/.test(apiKey)) throw new Error("PHOTON_API_KEY must be a Beta project key");
  if (!/^whsec_[A-Za-z0-9+/]+={0,2}$/.test(webhookSecret)) throw new Error("PHOTON_WEBHOOK_SECRET must be a destination signing secret");
  const decoded = Buffer.from(webhookSecret.slice(6), "base64");
  if (decoded.length < 24 || decoded.toString("base64") !== webhookSecret.slice(6)) throw new Error("PHOTON_WEBHOOK_SECRET requires at least 24 bytes in canonical base64");
  new Webhook(webhookSecret);
  const webhookPort = Number(env.PHOTON_WEBHOOK_PORT ?? "3012");
  if (!Number.isInteger(webhookPort) || webhookPort < 1 || webhookPort > 65535) throw new Error("Invalid PHOTON_WEBHOOK_PORT");
  const agentHandle = bound(env.PHOTON_AGENT_HANDLE);
  if (!/^\+[1-9][0-9]{7,14}$/.test(agentHandle) && !/^[\x21-\x7e]+@[^@\s]+\.[^@\s]+$/.test(agentHandle)) throw new Error("PHOTON_AGENT_HANDLE requires the assigned agent number or email");
  return { projectId, apiKey, webhookSecret, agentHandle, webhookPort, statePath: env.PHOTON_STATE_PATH ?? join(homedir(), ".local", "state", "stitch-photon-beta", "bridge.sqlite") };
}

export type Proof = { projectId: string; spaceId: string; humanId: string; handle: string; agentId: string; agentHandle: string };
export type Event = { id: string; messageId: string; proof: Proof; parts: Obj[] };
/** Advisory priority/diagnostic only: never substitutes for API consent handling. */
export function isStopEvent(event: Event): boolean {
  return event.parts.some(part => part.type === "text" && /^(STOP|STOPALL|UNSUBSCRIBE)(?:\b|$)/i.test(
    part.text.normalize("NFKC").replace(/[\p{Cf}\u3164\u115f\u1160\uffa0\u034f]/gu, "").trim().replace(/^[\p{P}\p{S}\s]+/u, "")));
}

class LocalApiError extends ProtocolError {
  endpoint: string;
  constructor(endpoint: string, status: number) { super(status, "Local Channels API rejected request"); this.endpoint = endpoint; }
}
function party(value: unknown, project: string, kind: string): Obj {
  const p = object(value);
  id(p.id, "usr"); bound(p.handle);
  if (p.projectId !== project || p.platform !== "imessage" || p.kind !== kind) fail("Photon sender identity mismatch");
  return p;
}
function space(value: unknown, proof: Proof): void {
  const s = object(value);
  if (s.id !== proof.spaceId || s.projectId !== proof.projectId || s.platform !== "imessage" || s.kind !== "direct" ||
    !Array.isArray(s.participantIds) || s.participantIds.length !== 2 || new Set(s.participantIds).size !== 2 ||
    !s.participantIds.includes(proof.humanId) || !s.participantIds.includes(proof.agentId)) fail("Photon direct-space proof mismatch");
}
export function parseBetaEvent(value: unknown, projectId: string, webhookId: string, agentHandle: string): Event {
  const e = object(value), data = object(e.data), m = object(data.object);
  id(e.id, "evt");
  if (e.id !== webhookId || e.apiVersion !== "2026-07-01" || e.source !== "imessage.photon" || e.type !== "message.received" ||
    e.app_installation_id !== undefined || data.projectId !== projectId || m.projectId !== projectId || m.platform !== "imessage" || m.status !== "received") fail();
  const human = party(m.sentBy, projectId, "human"), agent = party(m.receivedBy, projectId, "agent");
  if (human.id === agent.id || agent.handle !== agentHandle) fail();
  if (m.addressedTo !== undefined) { const addressed = party(m.addressedTo, projectId, "agent"); if (addressed.id !== agent.id || addressed.handle !== agent.handle) fail(); }
  const proof = { projectId, spaceId: id(object(m.space).id, "spc"), humanId: human.id, handle: human.handle, agentId: agent.id, agentHandle: agent.handle };
  space(m.space, proof);
  const content = object(m.content);
  const parts = content.type === "group" ? content.parts : [content];
  if (!Array.isArray(parts) || parts.length < 1 || parts.length > 10) fail("Unsupported Photon content");
  for (const p of parts) {
    object(p);
    if (p.type === "text") { if (p.format !== "plain") fail(); bound(p.text, 8000); }
    else if (p.type === "attachment") id(p.attachmentId, "att");
    else fail("Photon Beta supports only plain text and images");
  }
  if (parts.reduce((n: number, p: Obj) => n + (p.type === "text" ? Buffer.byteLength(p.text) : 0), 0) > 8000) fail();
  // Never leak a LINK code into a caption, draft, or partial mixed-content batch.
  if (parts.some((p: Obj) => p.type === "text" && linkCode(p.text) !== null) && parts.length !== 1) fail("LINK must be a standalone text message");
  return { id: e.id, messageId: id(m.id, "msg"), proof, parts };
}

export class BetaApi {
  private config: BetaConfig;
  private fetcher: typeof fetch;
  constructor(config: BetaConfig, fetcher: typeof fetch = fetch) { this.config = config; this.fetcher = fetcher; }
  async request(path: string, options: { body?: unknown; key?: string; binary?: boolean; signal?: AbortSignal } = {}): Promise<any> {
    // Paths are built exclusively from validated resource IDs, never webhook URLs.
    if (!/^\/(?:spaces|users|attachments|messages)(?:\/pho_(?:spc|usr|att)_[0-7][0-9a-hjkmnp-tv-z]{25}(?:\/content)?)?$/.test(path)) fail();
    const signal = options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000);
    signal.throwIfAborted();
    const response = await boundedOperation(this.fetcher(`https://api.photon.codes/v1/projects/${this.config.projectId}${path}`, {
      method: options.body === undefined ? "GET" : "POST", redirect: "error", signal,
      headers: { Authorization: `Bearer ${this.config.apiKey}`, ...(options.body === undefined ? {} : { "Content-Type": "application/json", "Idempotency-Key": options.key! }) },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    }), signal).catch(error => { if (error instanceof ProtocolError) throw error; throw new ProtocolError(502, "Photon Beta unreachable"); });
    if (!response.ok) { void response.body?.cancel(); throw new ProtocolError(response.status, "Photon Beta request failed"); }
    if (options.binary) {
      const mime = response.headers.get("content-type")?.split(";")[0] ?? "";
      if (!response.body) fail();
      if (!PHOTO_TYPES.has(mime)) {
        void response.body!.cancel().catch(() => {});
        throw new ProtocolError(415, "Unsupported photo type");
      }
      const raw = await downloadMedia({ mimeType: mime, stream: async () => response.body! }, PHOTO_TYPES, MAX_PHOTO_BYTES, "Photo", { signal });
      signal.throwIfAborted();
      return { raw, mime };
    }
    const max = 262_144;
    const reader = response.body?.getReader();
    if (!reader) throw new ProtocolError(502, "Photon Beta returned no body");
    const chunks: Buffer[] = []; let length = 0;
    try { await boundedOperation((async () => { for (;;) { signal.throwIfAborted(); const part = await reader.read(); if (part.done) break; length += part.value.length; if (length > max) throw new ProtocolError(413, "Photon response too large"); chunks.push(Buffer.from(part.value)); } })(), signal); }
    finally { void reader.cancel().catch(() => {}); reader.releaseLock(); }
    const raw = Buffer.concat(chunks);
    signal.throwIfAborted();
    try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw)); } catch { throw new ProtocolError(502, "Invalid Photon response"); }
  }
  async verify(proof: Proof, signal?: AbortSignal): Promise<void> {
    if (proof.projectId !== this.config.projectId || proof.agentHandle !== this.config.agentHandle) fail();
    space(await this.request(`/spaces/${proof.spaceId}`, { signal }), proof);
    const human = party(await this.request(`/users/${proof.humanId}`, { signal }), proof.projectId, "human");
    const agent = party(await this.request(`/users/${proof.agentId}`, { signal }), proof.projectId, "agent");
    if (human.id !== proof.humanId || human.handle !== proof.handle || agent.id !== proof.agentId || agent.handle !== proof.agentHandle) fail("Photon identity changed");
  }
}

export class BetaBridge {
  private store: BetaStore;
  private api: BetaApi;
  private client: Pick<ApiClient, "post">;
  private running = false;
  private sending = 0;
  constructor(store: BetaStore, api: BetaApi, client: Pick<ApiClient, "post">) { this.store = store; this.api = api; this.client = client; }
  async drain(projectId: string, agentHandle: string): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      const row = this.store.next(); if (!row) return;
      let retrySafe = true;
      let routeId: string | undefined;
      let isStop = false;
      const diagnostic = () => `event=${/^pho_evt_[0-7][0-9a-hjkmnp-tv-z]{25}$/.test(row.id) ? row.id : "invalid"} isSTOP=${isStop}`;
      try {
        const signal = AbortSignal.timeout(60_000);
        const event = parseBetaEvent(JSON.parse(row.body), projectId, row.id, agentHandle), p = event.proof;
        isStop = isStopEvent(event);
        routeId = p.spaceId;
        await this.api.verify(p, signal);
        for (const [index, part] of event.parts.entries()) {
          if (index < row.part) continue;
          signal.throwIfAborted();
          const common = { messageId: `${event.messageId}:part:${index}`, provider: "imessage" as const, spaceId: p.spaceId, senderId: p.handle };
          if (part.type === "text") {
            const code = linkCode(part.text);
            if (code !== null) {
              retrySafe = false; // One-use proof: a lost ACK cannot be replayed.
              const receipt = await this.postApi("/internal/channels/enroll", { ...common, spaceKind: "direct", code });
              if (object(receipt.body).status !== "active") fail("Enrollment outcome uncertain");
            } else {
              const receipt = await this.postApi("/internal/channels/inbound", { ...common, text: part.text });
              if (receipt.status !== 202 || object(receipt.body).accepted !== true) fail("Inbox outcome uncertain");
            }
          } else {
            await this.authorize(p.handle);
            const meta = object(await this.api.request(`/attachments/${part.attachmentId}`, { signal }));
            if (meta.id !== part.attachmentId || meta.projectId !== projectId || !PHOTO_TYPES.has(meta.contentType) || !Number.isSafeInteger(meta.sizeBytes) || meta.sizeBytes <= 0 || meta.sizeBytes > MAX_PHOTO_BYTES) fail("Unsupported Photon image");
            const { raw, mime } = await this.api.request(`/attachments/${part.attachmentId}/content`, { binary: true, signal });
            if (mime !== meta.contentType || raw.length !== meta.sizeBytes) fail("Photon attachment mismatch");
            await this.api.verify(p, signal);
            retrySafe = false; // Photo storage may have an uncertain external effect.
            const receipt = await this.postApi("/internal/channels/inbound-photo", parsePhoto({ ...common, attachmentId: part.attachmentId, mimeType: meta.contentType, dataBase64: raw.toString("base64") }));
            if (receipt.status !== 202 || object(receipt.body).accepted !== true) fail("Photo receipt outcome uncertain");
          }
          retrySafe = false; // A failed local checkpoint also stays uncertain.
          this.store.checkpoint(row.id, index + 1);
          retrySafe = true;
        }
        this.store.finish(row.id, "done");
        // Route cache availability must not prevent or undo consent commands.
        try { this.store.saveRoute(p.spaceId, p); }
        catch { console.warn(`Photon reply route unavailable; inbound receipt completed. ${diagnostic()}`); }
      } catch (error) {
        // Local 404 is ambiguous (unknown contact OR unmounted/disabled router).
        // Never treat it, or any Photon REST error, as proof of consent refusal.
        if (error instanceof LocalApiError && (error.status === 403
            || (error.endpoint === "/internal/channels/enroll" && error.status === 400))) {
          this.store.finish(row.id, "done");
          if (routeId) this.store.forgetRoute(routeId);
          console.warn(`Photon event refused; private payload removed. ${diagnostic()}`);
        } else if (retrySafe && transient(error) && this.store.retry(row.id)) {
          console.warn(`Photon event deferred for bounded retry. ${diagnostic()}`);
        } else {
          this.store.finish(row.id, "blocked");
          console.warn(`Photon event blocked; retries exhausted or outcome uncertain. Inspect ledger and apply unresolved STOP in settings. ${diagnostic()}`);
        }
      }
    } finally { this.running = false; }
  }
  async send(message: Outbound): Promise<void> {
    this.sending++;
    try { await this.sendReserved(message); } finally { this.sending--; }
  }
  private async postApi(path: Parameters<ApiClient["post"]>[0], body: Parameters<ApiClient["post"]>[1]) {
    try { return await this.client.post(path, body); }
    catch (error) { if (error instanceof ProtocolError) throw new LocalApiError(path, error.status); throw error; }
  }
  private async authorize(handle: string): Promise<void> {
    const response = await this.postApi("/internal/channels/authorize", { provider: "imessage", senderId: handle });
    if (response.status !== 200 || object(response.body).allowed !== true) throw new ProtocolError(502, "Contact authorization unavailable");
  }
  private async sendReserved(message: Outbound): Promise<void> {
    parseMessage(message, false);
    if (message.provider !== "imessage") fail();
    const hash = digest(JSON.stringify([message.provider, message.spaceId, message.text]));
    if (this.store.replySent(message.messageId, hash)) return;
    const proof = this.store.route(message.spaceId) as Proof | undefined;
    if (!proof) throw new ProtocolError(404, "Unknown verified Photon space");
    const signal = AbortSignal.timeout(4_000);
    await this.api.verify(proof, signal);
    await this.authorize(proof.handle);
    signal.throwIfAborted();
    // No provider effect has begun; consent/verification failures reserve nothing.
    if (!this.store.reserveReply(message.messageId, hash)) return;
    const result = object(await this.api.request("/messages", { signal, key: `stitch:${digest(message.messageId)}`, body: {
      senderId: proof.agentId, spaceId: proof.spaceId, content: { type: "text", format: "plain", text: message.text },
    } }));
    if (result.projectId !== proof.projectId || result.platform !== "imessage" || !["dispatched", "accepted", "delivered"].includes(result.status)) fail("Photon send outcome uncertain");
    id(result.id, "msg"); space(result.space, proof);
    const sender = party(result.sentBy, proof.projectId, "agent");
    if (sender.id !== proof.agentId || sender.handle !== proof.agentHandle) fail("Photon send outcome uncertain");
    this.store.sent(message.messageId);
  }
  busy(): boolean { return this.running || this.sending > 0; }
}

export function createBetaWebhook(config: BetaConfig, store: BetaStore): Server {
  const verifier = new Webhook(config.webhookSecret);
  const server = createServer({ connectionsCheckingInterval: 1000 }, async (request, response) => {
    const deadline = setTimeout(() => request.destroy(), 10_000);
    deadline.unref();
    try {
      if (request.method !== "POST" || request.url !== "/photon/webhook") throw new ProtocolError(404, "Not found");
      if (request.headers["content-type"]?.split(";")[0] !== "application/json") throw new ProtocolError(415, "Expected application/json");
      const chunks: Buffer[] = []; let length = 0;
      for await (const chunk of request) { length += chunk.length; if (length > 65_536) throw new ProtocolError(413, "Webhook too large"); chunks.push(Buffer.from(chunk)); }
      const raw = Buffer.concat(chunks);
      try { new TextDecoder("utf-8", { fatal: true }).decode(raw); } catch { throw new ProtocolError(400, "Invalid webhook encoding"); }
      const headers: Record<string, string> = {};
      for (const key of ["webhook-id", "webhook-timestamp", "webhook-signature"]) { const value = request.headers[key]; if (typeof value !== "string") throw new ProtocolError(400, "Invalid webhook signature"); headers[key] = value; }
      let event: unknown;
      try { event = verifier.verify(raw, headers); } catch { throw new ProtocolError(400, "Invalid webhook signature"); }
      const parsed = parseBetaEvent(event, config.projectId, headers["webhook-id"], config.agentHandle);
      store.accept(headers["webhook-id"], raw.toString("utf8"), isStopEvent(parsed));
      response.writeHead(204).end();
    } catch (error) { const status = error instanceof ProtocolError ? error.status : 503; response.writeHead(status, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "Photon webhook not accepted" })); }
    finally { clearTimeout(deadline); }
  });
  server.maxConnections = 64;
  server.requestTimeout = 10_000; server.headersTimeout = 5_000; server.setTimeout(10_000, socket => socket.destroy());
  return server;
}
