import { Spectrum } from "spectrum-ts";
import { terminal } from "spectrum-ts/providers/terminal";
import { imessage } from "spectrum-ts/providers/imessage";
import { ApiClient, TerminalOutbox, createSendServer } from "./bridge.ts";
import { configureProviderEnvironment, validateSecret } from "./protocol.ts";
import { forwardProviderMessage } from "./photos.ts";

const secret = validateSecret(process.env.CHANNELS_SHARED_SECRET ?? "");
const client = new ApiClient(process.env.CHANNELS_API_URL ?? "http://127.0.0.1:8000", secret);
const provider = configureProviderEnvironment(process.env);
const port = Number(process.env.CHANNELS_BRIDGE_PORT ?? "3011");
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid CHANNELS_BRIDGE_PORT");
const outbox = new TerminalOutbox(5_000, provider);
const server = createSendServer(secret, outbox);
await new Promise<void>((resolve, reject) => {
  server.once("error", reject);
  server.listen(port, "127.0.0.1", resolve);
});

// Terminal is the default. Only explicit imessage configuration can connect a cloud line.
// Flattening preserves SDK child IDs for combined text/photo and multi-photo messages.
const app = provider === "terminal"
  ? await Spectrum({ providers: [terminal.config()], options: { flattenGroups: true } })
  : await Spectrum({ providers: [imessage.config()], options: { flattenGroups: true } });
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
console.log(`Channels ${provider} bridge listening on 127.0.0.1:${port}. Inbox receipts only; no completed story guaranteed.`);
try {
  for await (const [space, message] of app.messages) {
    if (stopping) break;
    if (!message.sender || message.platform !== provider || message.direction !== "inbound") {
      console.warn("Ignoring unsupported sender, provider or message direction."); continue;
    }
    try {
      outbox.register(space.id, (text) => space.send(text), provider);
      for (const receipt of await forwardProviderMessage(client, space.id, message)) {
        console.log(`Inbox receipt HTTP ${receipt.status}; this does not mean a story was generated.`);
      }
    } catch (error) {
      // Errors intentionally never include the text, shared secret or provider response body.
      console.warn(error instanceof Error ? error.message : "Inbound delivery failed; receipt not confirmed.");
    }
  }
} finally { await stop(); }
