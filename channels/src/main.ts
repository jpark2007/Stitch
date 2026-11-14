import { Spectrum } from "spectrum-ts";
import { terminal } from "spectrum-ts/providers/terminal";
import { ApiClient, TerminalOutbox, createSendServer } from "./bridge.ts";
import { parseMessage, validateSecret } from "./protocol.ts";

const secret = validateSecret(process.env.CHANNELS_SHARED_SECRET ?? "");
const client = new ApiClient(process.env.CHANNELS_API_URL ?? "http://127.0.0.1:8000", secret);
const port = Number(process.env.CHANNELS_BRIDGE_PORT ?? "3011");
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid CHANNELS_BRIDGE_PORT");
const outbox = new TerminalOutbox();
const server = createSendServer(secret, outbox);
await new Promise<void>((resolve, reject) => {
  server.once("error", reject);
  server.listen(port, "127.0.0.1", resolve);
});

// Only a projectless local provider is configured. No iMessage, calls or model clients.
// Spectrum otherwise loads project credentials from inherited env and fetches that
// project's cloud configuration, even with only a terminal provider configured.
delete process.env.SPECTRUM_PROJECT_ID;
delete process.env.SPECTRUM_PROJECT_SECRET;
delete process.env.SPECTRUM_WEBHOOK_SECRET;
const app = await Spectrum({ providers: [terminal.config()] });
let stopping = false;
let ticking = false;
const interval = setInterval(async () => {
  if (ticking || stopping) return;
  ticking = true;
  try { await client.post("/internal/channels/tick", {}); }
  catch { console.warn("Channels tick unavailable; no scheduler work confirmed."); }
  finally { ticking = false; }
}, 60_000);
const stop = async () => {
  if (stopping) return;
  stopping = true;
  clearInterval(interval);
  server.closeAllConnections();
  server.close();
  await app.stop();
};
process.once("SIGINT", () => { void stop(); });
process.once("SIGTERM", () => { void stop(); });
console.log(`Channels terminal bridge listening on 127.0.0.1:${port}. Inbox receipts only; no story generation yet.`);
try {
  for await (const [space, message] of app.messages) {
    if (stopping) break;
    if (message.content.type !== "text" || !message.sender) {
      console.warn("Only text with a sender is supported in this slice."); continue;
    }
    try {
      const envelope = parseMessage({ messageId: message.id, provider: "terminal", spaceId: space.id, senderId: message.sender.id, text: message.content.text }, true);
      outbox.register(space.id, (text) => space.send(text));
      const receipt = await client.post("/internal/channels/inbound", envelope);
      console.log(`Inbox receipt HTTP ${receipt.status}; this does not mean a story was generated.`);
    } catch (error) {
      // Errors intentionally never include the text, shared secret or provider response body.
      console.warn(error instanceof Error ? error.message : "Inbound delivery failed; receipt not confirmed.");
    }
  }
} finally { await stop(); }
