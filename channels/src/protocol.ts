import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";

export const MAX_BODY_BYTES = 16_384;
export const MAX_TEXT_BYTES = 8_000;
export const MAX_PHOTO_BYTES = 20_971_520;
export const MAX_PHOTO_BODY_BYTES = 27_978_412;
export const MAX_VOICE_BYTES = 20_971_520;
export const MAX_VOICE_BODY_BYTES = 27_978_412;
export const VOICE_TYPES = new Set(["audio/mpeg", "audio/mp3", "audio/mpga", "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/wav", "audio/x-wav", "audio/webm", "audio/ogg", "audio/flac", "audio/x-flac"]);
export const PHOTO_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"]);
export const CLOCK_WINDOW_SECONDS = 300;
export type Provider = "terminal" | "imessage";
export type Outbound = { messageId: string; provider: Provider; spaceId: string; text: string };
export type Inbound = Outbound & { senderId: string };
export type Authorization = { provider: Provider; senderId: string };
export type InboundPhoto = Omit<Inbound, "text"> & { attachmentId: string; mimeType: string; dataBase64: string; caption?: string };
export type InboundVoice = Omit<Inbound, "text"> & { attachmentId: string; mimeType: string; dataBase64: string };

export class ProtocolError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

export function validateSecret(secret: string): string {
  // Same explicit edge-whitespace set as api/channels/models.py; reject rather
  // than trim so both peers authenticate the exact configured UTF-8 bytes.
  if (Buffer.byteLength(secret) < 32 ||
      /^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]|[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]$/u.test(secret) ||
      /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(secret)) {
    throw new Error("CHANNELS_SHARED_SECRET requires at least 32 UTF-8 bytes and no edge whitespace");
  }
  return secret;
}

function bounded(value: unknown, limit: number): value is string {
  // Explicit shared JS/Python whitespace and scalar-value rules avoid divergent byte validation.
  return typeof value === "string" && !/^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]*$/u.test(value) &&
    !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value) && Buffer.byteLength(value) <= limit;
}

function recordWithKeys(value: unknown, required: string[], optional: string[] = []): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ProtocolError(422, "Invalid message");
  const record = value as Record<string, unknown>;
  if (required.some((key) => !(key in record)) || Object.keys(record).some((key) => !required.includes(key) && !optional.includes(key))) throw new ProtocolError(422, "Invalid message");
  return record;
}

export function parseAuthorization(value: unknown): Authorization {
  const record = recordWithKeys(value, ["provider", "senderId"]);
  if (!bounded(record.senderId, 256) || (record.provider !== "terminal" && record.provider !== "imessage")) throw new ProtocolError(422, "Invalid authorization request");
  return record as Authorization;
}

export function parsePhoto(value: unknown): InboundPhoto {
  const record = recordWithKeys(value, ["messageId", "provider", "spaceId", "senderId", "attachmentId", "mimeType", "dataBase64"], ["caption"]);
  parseAuthorization({ provider: record.provider, senderId: record.senderId });
  if (![record.messageId, record.spaceId, record.attachmentId].every((id) => bounded(id, 256)) ||
      typeof record.mimeType !== "string" || !PHOTO_TYPES.has(record.mimeType)) throw new ProtocolError(422, "Invalid photo metadata");
  const caption = record.caption ?? "";
  if (typeof caption !== "string" || Buffer.byteLength(caption) > MAX_TEXT_BYTES ||
      /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(caption) || record.caption === null) throw new ProtocolError(422, "Invalid photo caption");
  if (typeof record.dataBase64 !== "string" || record.dataBase64.length === 0 ||
      record.dataBase64.length > 4 * Math.ceil(MAX_PHOTO_BYTES / 3) || record.dataBase64.length % 4 !== 0 ||
      !/^[A-Za-z0-9+/]*={0,2}$/.test(record.dataBase64)) throw new ProtocolError(422, "Invalid photo encoding");
  const decoded = Buffer.from(record.dataBase64, "base64");
  if (decoded.length === 0 || decoded.length > MAX_PHOTO_BYTES || decoded.toString("base64") !== record.dataBase64) throw new ProtocolError(422, "Invalid photo encoding");
  return record as InboundPhoto;
}

export function parseVoice(value: unknown): InboundVoice {
  const record = recordWithKeys(value, ["messageId", "provider", "spaceId", "senderId", "attachmentId", "mimeType", "dataBase64"]);
  parseAuthorization({ provider: record.provider, senderId: record.senderId });
  if (![record.messageId, record.spaceId, record.attachmentId].every((id) => bounded(id, 256)) ||
      typeof record.mimeType !== "string" || !VOICE_TYPES.has(record.mimeType)) throw new ProtocolError(422, "Invalid voice metadata");
  if (typeof record.dataBase64 !== "string" || record.dataBase64.length === 0 ||
      record.dataBase64.length > 4 * Math.ceil(MAX_VOICE_BYTES / 3) || record.dataBase64.length % 4 !== 0 ||
      !/^[A-Za-z0-9+/]*={0,2}$/.test(record.dataBase64)) throw new ProtocolError(422, "Invalid voice encoding");
  const decoded = Buffer.from(record.dataBase64, "base64");
  if (decoded.length === 0 || decoded.length > MAX_VOICE_BYTES || decoded.toString("base64") !== record.dataBase64) throw new ProtocolError(422, "Invalid voice encoding");
  return record as InboundVoice;
}

/** Explicit opt-in only; terminal mode cannot inherit Spectrum cloud credentials. */
export function configureProviderEnvironment(env: NodeJS.ProcessEnv): Provider {
  const provider = env.CHANNELS_PROVIDER ?? "terminal";
  if (provider === "terminal") {
    delete env.SPECTRUM_PROJECT_ID;
    delete env.SPECTRUM_PROJECT_SECRET;
    delete env.SPECTRUM_WEBHOOK_SECRET;
    return provider;
  }
  if (provider !== "imessage") throw new Error("CHANNELS_PROVIDER must be terminal or imessage");
  const project = env.SPECTRUM_PROJECT_ID?.trim();
  if (!project || !env.SPECTRUM_PROJECT_SECRET?.trim()) throw new Error("iMessage requires Stable SPECTRUM_PROJECT_ID and SPECTRUM_PROJECT_SECRET");
  if (project.startsWith("pho_prj_")) throw new Error("Photon Beta project is incompatible; use a Stable Spectrum project");
  return provider;
}

export function parseMessage(value: unknown, inbound: true): Inbound;
export function parseMessage(value: unknown, inbound: false): Outbound;
export function parseMessage(value: unknown, inbound: boolean): Inbound | Outbound {
  const keys = inbound ? ["messageId", "provider", "spaceId", "senderId", "text"] : ["messageId", "provider", "spaceId", "text"];
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ProtocolError(422, "Invalid message");
  const record = value as Record<string, unknown>;
  if (Object.keys(record).length !== keys.length || Object.keys(record).some((key) => !keys.includes(key)) ||
      !bounded(record.messageId, 256) || !bounded(record.spaceId, 256) ||
      !bounded(record.text, MAX_TEXT_BYTES) || (inbound && !bounded(record.senderId, 256)) ||
      (record.provider !== "terminal" && record.provider !== "imessage")) throw new ProtocolError(422, "Invalid message");
  return record as Inbound | Outbound;
}

export function signature(secret: string, method: string, path: string, timestamp: string, nonce: string, body: Uint8Array): string {
  return createHmac("sha256", validateSecret(secret))
    .update(`${method}\n${path}\n${timestamp}\n${nonce}\n`).update(body).digest("hex");
}

export function signedHeaders(secret: string, path: string, body: Uint8Array, now = Date.now()): Record<string, string> {
  const timestamp = String(Math.floor(now / 1000));
  const nonce = randomUUID();
  return {
    "Content-Type": "application/json",
    "X-Channels-Timestamp": timestamp,
    "X-Channels-Nonce": nonce,
    "X-Channels-Signature": signature(secret, "POST", path, timestamp, nonce, body),
  };
}

/** Rejects replay for this process. Capacity fails closed; expired entries alone are evicted. */
export class Authenticator {
  private seen = new Map<string, number>();
  private secret: string;
  private capacity: number;
  constructor(secret: string, capacity = 10_000) { this.secret = validateSecret(secret); this.capacity = capacity; }
  verify(method: string, path: string, headers: Headers, body: Uint8Array, now = Date.now()): void {
    const timestamp = headers.get("x-channels-timestamp") ?? "";
    const nonce = headers.get("x-channels-nonce") ?? "";
    const provided = headers.get("x-channels-signature") ?? "";
    const seconds = Math.floor(now / 1000);
    if (!/^[0-9]{1,12}$/.test(timestamp) || Math.abs(seconds - Number(timestamp)) > CLOCK_WINDOW_SECONDS ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(nonce) || !/^[0-9a-f]{64}$/.test(provided)) {
      throw new ProtocolError(401, "Invalid channel authentication");
    }
    const expected = signature(this.secret, method, path, timestamp, nonce, body);
    if (!timingSafeEqual(Buffer.from(provided, "hex"), Buffer.from(expected, "hex"))) throw new ProtocolError(401, "Invalid channel authentication");
    for (const [key, expires] of this.seen) if (expires < seconds) this.seen.delete(key);
    if (this.seen.has(nonce)) throw new ProtocolError(409, "Replayed request");
    if (this.seen.size >= this.capacity) throw new ProtocolError(503, "Authentication capacity reached");
    // A future timestamp stays acceptable until timestamp + window, not now + window.
    this.seen.set(nonce, Number(timestamp) + CLOCK_WINDOW_SECONDS);
  }
}
