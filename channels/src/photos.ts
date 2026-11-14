import type { Attachment, Message } from "spectrum-ts";
import type { ApiClient, Receipt } from "./bridge.ts";
import { MAX_PHOTO_BYTES, PHOTO_TYPES, ProtocolError, parseAuthorization, parseMessage, parsePhoto, type Provider } from "./protocol.ts";

type PhotoSource = Pick<Attachment, "id" | "mimeType" | "size" | "stream">;
export type PhotoIdentity = { messageId: string; provider: Provider; spaceId: string; senderId: string };
export type ProviderMessage = Pick<Message, "id" | "platform" | "sender" | "content">;
type PhotoOptions = { timeoutMs?: number };

/** Only consume the SDK's authenticated attachment stream; never fetch a supplied URL. */
export async function downloadPhoto(attachment: PhotoSource, options: PhotoOptions = {}): Promise<Buffer> {
  if (!PHOTO_TYPES.has(attachment.mimeType)) throw new ProtocolError(415, "Unsupported photo type");
  if (attachment.size !== undefined && (!Number.isSafeInteger(attachment.size) || attachment.size <= 0 || attachment.size > MAX_PHOTO_BYTES)) throw new ProtocolError(413, "Invalid photo size");
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let timedOut = false;
  let timer: ReturnType<typeof setTimeout>;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      timedOut = true;
      reject(new ProtocolError(504, "Photo download timed out"));
      void reader?.cancel().catch(() => {});
    }, options.timeoutMs ?? 15_000);
  });
  // If opening the SDK stream itself is late, cancel it as soon as it arrives.
  const opening = Promise.resolve().then(() => attachment.stream()).then((stream) => {
    if (timedOut) { void stream.cancel().catch(() => {}); throw new ProtocolError(504, "Photo download timed out"); }
    return stream;
  });
  try {
    const stream = await Promise.race([opening, deadline]);
    reader = stream.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    for (;;) {
      const part = await Promise.race([reader.read(), deadline]);
      if (part.done) break;
      size += part.value.byteLength;
      if (size > MAX_PHOTO_BYTES) throw new ProtocolError(413, "Photo exceeds 20 MiB");
      chunks.push(part.value);
    }
    if (!size) throw new ProtocolError(422, "Photo is empty");
    return Buffer.concat(chunks, size);
  } catch (error) {
    void reader?.cancel().catch(() => {});
    if (error instanceof ProtocolError) throw error;
    throw new ProtocolError(502, "Photo stream failed");
  } finally {
    clearTimeout(timer!);
    try { reader?.releaseLock(); } catch { /* A cancellation may still be settling. */ }
  }
}

export async function forwardPhoto(client: Pick<ApiClient, "post">, identity: PhotoIdentity, attachment: PhotoSource, caption = "", options: PhotoOptions = {}): Promise<Receipt> {
  // Validate all metadata before any download, without accepting media URLs or family IDs.
  parsePhoto({ ...identity, attachmentId: attachment.id, mimeType: attachment.mimeType, dataBase64: "AA==", caption });
  const authorization = await client.post("/internal/channels/authorize", parseAuthorization({ provider: identity.provider, senderId: identity.senderId }));
  if (!authorization.body || typeof authorization.body !== "object" || (authorization.body as Record<string, unknown>).allowed !== true) throw new ProtocolError(403, "Photo sender is not authorized");
  const bytes = await downloadPhoto(attachment, options);
  return client.post("/internal/channels/inbound-photo", { ...identity, attachmentId: attachment.id, mimeType: attachment.mimeType, dataBase64: bytes.toString("base64"), caption });
}

/** Group children retain their own SDK IDs/order; text and photos are not dropped. */
export async function forwardProviderMessage(client: Pick<ApiClient, "post">, spaceId: string, message: ProviderMessage): Promise<Receipt[]> {
  if (message.content.type === "group") {
    const receipts: Receipt[] = [];
    for (const child of message.content.items) receipts.push(...await forwardProviderMessage(client, spaceId, child));
    return receipts;
  }
  if (!message.sender || (message.platform !== "terminal" && message.platform !== "imessage")) throw new ProtocolError(422, "Unsupported sender or provider");
  const identity: PhotoIdentity = { messageId: message.id, provider: message.platform, spaceId, senderId: message.sender.id };
  if (message.content.type === "text") return [await client.post("/internal/channels/inbound", parseMessage({ ...identity, text: message.content.text }, true))];
  if (message.content.type === "attachment") return [await forwardPhoto(client, identity, message.content)];
  throw new ProtocolError(415, "Only text and photo attachments are supported");
}
