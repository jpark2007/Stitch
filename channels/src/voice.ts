import type { Voice } from "spectrum-ts";
import type { ApiClient, Receipt } from "./bridge.ts";
import { downloadMedia, type DownloadOptions } from "./media.ts";
import { MAX_VOICE_BYTES, VOICE_TYPES, ProtocolError, parseAuthorization, parseVoice, type Provider } from "./protocol.ts";

type VoiceSource = Pick<Voice, "id" | "mimeType" | "size" | "stream">;
export type VoiceIdentity = { messageId: string; provider: Provider; spaceId: string; senderId: string };

export function downloadVoice(voice: VoiceSource, options: DownloadOptions = {}): Promise<Buffer> {
  // Native iMessage audio/x-caf stays CAF here. The authenticated backend must
  // decode it locally before transcription; never relabel its bytes as WAV.
  return downloadMedia(voice, VOICE_TYPES, MAX_VOICE_BYTES, "Voice", options);
}

export async function forwardVoice(client: Pick<ApiClient, "post">, identity: VoiceIdentity, voice: VoiceSource, options: DownloadOptions = {}): Promise<Receipt> {
  // Voice.id is optional in the actual SDK. Its child message ID is stable on redelivery.
  const attachmentId = voice.id ?? identity.messageId;
  parseVoice({ ...identity, attachmentId, mimeType: voice.mimeType, dataBase64: "AA==" });
  const authorization = await client.post("/internal/channels/authorize", parseAuthorization({ provider: identity.provider, senderId: identity.senderId }));
  if (!authorization.body || typeof authorization.body !== "object" || (authorization.body as Record<string, unknown>).allowed !== true) throw new ProtocolError(403, "Voice sender is not authorized");
  const bytes = await downloadVoice(voice, options);
  return client.post("/internal/channels/inbound-voice", { ...identity, attachmentId, mimeType: voice.mimeType, dataBase64: bytes.toString("base64") });
}
