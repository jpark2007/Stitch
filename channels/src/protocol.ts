import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";

export const MAX_BODY_BYTES = 16_384;
export const MAX_TEXT_BYTES = 8_000;
export const CLOCK_WINDOW_SECONDS = 300;
export type Provider = "terminal" | "imessage";
export type Outbound = { messageId: string; provider: Provider; spaceId: string; text: string };
export type Inbound = Outbound & { senderId: string };

export class ProtocolError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

export function validateSecret(secret: string): string {
  if (Buffer.byteLength(secret) < 32) throw new Error("CHANNELS_SHARED_SECRET must be at least 32 UTF-8 bytes");
  return secret;
}

function bounded(value: unknown, limit: number): value is string {
  // Explicit shared JS/Python whitespace and scalar-value rules avoid divergent byte validation.
  return typeof value === "string" && !/^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]*$/u.test(value) &&
    !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value) && Buffer.byteLength(value) <= limit;
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
