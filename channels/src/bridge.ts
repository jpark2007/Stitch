import { createHash } from "node:crypto";
import { createServer, type IncomingMessage, type Server } from "node:http";
import { Authenticator, MAX_BODY_BYTES, MAX_PHOTO_BODY_BYTES, ProtocolError, parseAuthorization, parseMessage, parsePhoto, signedHeaders, validateSecret, type Authorization, type Inbound, type InboundPhoto, type Outbound, type Provider } from "./protocol.ts";
import { MAX_VOICE_BODY_BYTES, parseVoice, type InboundVoice } from "./protocol.ts";

export type Receipt = { status: number; body: unknown };
type ClientOptions = { timeoutMs?: number; attempts?: number; fetch?: typeof fetch; delay?: (ms: number) => Promise<void> };

/** Restrict this local foundation to loopback HTTP; never send the shared secret to redirects. */
export function localApiUrl(value: string): URL {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("CHANNELS_API_URL must be a loopback HTTP origin");
  return url;
}

export class ApiClient {
  private origin: URL;
  private secret: string;
  private options: ClientOptions;
  constructor(origin: string, secret: string, options: ClientOptions = {}) {
    this.origin = localApiUrl(origin); this.secret = validateSecret(secret); this.options = options;
  }
  async post(path: "/internal/channels/inbound" | "/internal/channels/tick" | "/internal/channels/authorize" | "/internal/channels/inbound-photo" | "/internal/channels/inbound-voice", value: Inbound | InboundPhoto | InboundVoice | Authorization | Record<string, never>): Promise<Receipt> {
    if (path === "/internal/channels/inbound") parseMessage(value, true);
    else if (path === "/internal/channels/inbound-photo") parsePhoto(value);
    else if (path === "/internal/channels/inbound-voice") parseVoice(value);
    else if (path === "/internal/channels/authorize") parseAuthorization(value);
    else if (path !== "/internal/channels/tick" || !value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length !== 0) throw new ProtocolError(422, "Invalid request");
    const body = Buffer.from(JSON.stringify(value));
    const bodyLimit = path === "/internal/channels/inbound-photo" ? MAX_PHOTO_BODY_BYTES : path === "/internal/channels/inbound-voice" ? MAX_VOICE_BODY_BYTES : MAX_BODY_BYTES;
    if (body.length > bodyLimit) throw new ProtocolError(413, "Request too large");
    const attempts = this.options.attempts ?? 3;
    for (let attempt = 0; attempt < attempts; attempt++) {
      try {
        const response = await (this.options.fetch ?? fetch)(new URL(path, this.origin), {
          method: "POST", headers: signedHeaders(this.secret, path, body), body,
          redirect: "error", signal: AbortSignal.timeout(this.options.timeoutMs ?? 5_000),
        });
        const chunks: Uint8Array[] = [];
        let size = 0;
        if (response.body) {
          const reader = response.body.getReader();
          try {
            for (;;) {
              const result = await reader.read();
              if (result.done) break;
              size += result.value.length;
              if (size > MAX_BODY_BYTES) { await reader.cancel(); throw new ProtocolError(502, "API response too large"); }
              chunks.push(result.value);
            }
          } finally { reader.releaseLock(); }
        }
        if (!response.ok) {
          if ((response.status === 408 || response.status === 429 || response.status >= 500) && attempt + 1 < attempts) {
            await (this.options.delay ?? sleep)(250 * 2 ** attempt); continue;
          }
          throw new ProtocolError(response.status, `API rejected request (${response.status})`);
        }
        let payload: unknown;
        try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
        catch { throw new ProtocolError(502, "API returned invalid JSON"); }
        return { status: response.status, body: payload };
      } catch (error) {
        if (error instanceof ProtocolError) throw error;
        if (attempt + 1 === attempts) throw new ProtocolError(502, "API unreachable or timed out; receipt not confirmed");
        await (this.options.delay ?? sleep)(250 * 2 ** attempt);
      }
    }
    throw new ProtocolError(502, "No request attempts configured");
  }
}
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

type Sender = (text: string) => Promise<unknown>;
type Delivery = { fingerprint: string; result: Promise<void> };

/** One explicitly configured provider. Records stay for the process lifetime, including uncertain failures. */
export class TerminalOutbox {
  private spaces = new Map<string, Sender>();
  private deliveries = new Map<string, Delivery>();
  private timeoutMs: number;
  private provider: Provider;
  constructor(timeoutMs = 5_000, provider: Provider = "terminal") { this.timeoutMs = timeoutMs; this.provider = provider; }
  register(spaceId: string, send: Sender, provider: Provider = "terminal"): void {
    if (provider !== this.provider) throw new ProtocolError(422, "Provider is not enabled");
    const key = JSON.stringify([provider, spaceId]);
    if (!this.spaces.has(key) && this.spaces.size >= 1_000) throw new ProtocolError(503, "Space capacity reached");
    this.spaces.set(key, send);
  }
  async send(message: Outbound): Promise<void> {
    if (message.provider !== this.provider) throw new ProtocolError(422, this.provider === "terminal" ? "Only terminal delivery is enabled" : "Only configured iMessage delivery is enabled");
    const key = JSON.stringify([message.provider, message.messageId]);
    const fingerprint = createHash("sha256").update(JSON.stringify([message.provider, message.spaceId, message.text])).digest("hex");
    const existing = this.deliveries.get(key);
    if (existing) {
      if (existing.fingerprint !== fingerprint) throw new ProtocolError(409, "Message ID already used for different content");
      return existing.result;
    }
    const sender = this.spaces.get(JSON.stringify([message.provider, message.spaceId]));
    if (!sender) throw new ProtocolError(404, "Unknown provider space");
    if (this.deliveries.size >= 10_000) throw new ProtocolError(503, "Delivery capacity reached");
    // Promise callback defers provider work until the idempotency record exists.
    let timer: ReturnType<typeof setTimeout>;
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("Delivery deadline")), this.timeoutMs);
    });
    const result = Promise.race([Promise.resolve().then(() => sender(message.text)), deadline]).then(() => undefined)
      .catch(() => { throw new ProtocolError(502, "Provider delivery uncertain; retained without retry"); })
      .finally(() => clearTimeout(timer));
    this.deliveries.set(key, { fingerprint, result });
    return result;
  }
}

async function readBody(request: IncomingMessage): Promise<Buffer> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new ProtocolError(413, "Request too large");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

export function createSendServer(secret: string, outbox: TerminalOutbox): Server {
  const auth = new Authenticator(secret);
  const server = createServer(async (request, response) => {
    try {
      if (request.method !== "POST" || request.url !== "/send") throw new ProtocolError(404, "Not found");
      if (request.headers["content-type"]?.split(";")[0] !== "application/json") throw new ProtocolError(415, "Expected application/json");
      const raw = await readBody(request);
      const headers = new Headers();
      for (const [name, value] of Object.entries(request.headers)) {
        if (typeof value === "string") headers.set(name, value);
      }
      auth.verify("POST", "/send", headers, raw);
      let parsed: unknown;
      try { parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw)); } catch { throw new ProtocolError(422, "Invalid JSON"); }
      await outbox.send(parseMessage(parsed, false));
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end('{"status":"sent"}');
    } catch (error) {
      const failure = error instanceof ProtocolError ? error : new ProtocolError(500, "Bridge error");
      if (!response.destroyed) {
        response.writeHead(failure.status, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: failure.message }));
      }
    }
  });
  server.requestTimeout = 10_000;
  server.headersTimeout = 10_000;
  server.setTimeout(15_000, (socket) => socket.destroy());
  return server;
}
