import type { Attachment, Message } from "spectrum-ts";
import type { ApiClient, Receipt } from "./bridge.ts";
import { downloadMedia } from "./media.ts";
import { forwardVoice } from "./voice.ts";
import { MAX_PHOTO_BYTES, PHOTO_TYPES, ProtocolError, parseAuthorization, parseMessage, parsePhoto, type Provider } from "./protocol.ts";

type PhotoSource = Pick<Attachment, "id" | "mimeType" | "size" | "stream">;
export type PhotoIdentity = { messageId: string; provider: Provider; spaceId: string; senderId: string };
export type ProviderMessage = Pick<Message, "id" | "platform" | "sender" | "content">;
type PhotoOptions = { timeoutMs?: number };

/** Only consume the SDK's authenticated attachment stream; never fetch a supplied URL. */
export async function downloadPhoto(attachment: PhotoSource, options: PhotoOptions = {}): Promise<Buffer> {
  return downloadMedia(attachment, PHOTO_TYPES, MAX_PHOTO_BYTES, "Photo", options);
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
  if (message.content.type === "voice") return [await forwardVoice(client, identity, message.content)];
  throw new ProtocolError(415, "Only text, photo attachments and voice notes are supported");
}
