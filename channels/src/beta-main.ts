import { ApiClient, createSendServer } from "./bridge.ts";
import { BetaStore } from "./beta-store.ts";
import { betaConfig, BetaApi, BetaBridge, createBetaWebhook } from "./beta.ts";
import { validateSecret } from "./protocol.ts";

export async function startBeta(): Promise<void> {
  const config = betaConfig(process.env);
  const secret = validateSecret(process.env.CHANNELS_SHARED_SECRET ?? "");
  const client = new ApiClient(process.env.CHANNELS_API_URL ?? "http://127.0.0.1:8000", secret, { attempts: 1 });
  const port = Number(process.env.CHANNELS_BRIDGE_PORT ?? "3011");
  if (!Number.isInteger(port) || port < 1 || port > 65535 || port === config.webhookPort) throw new Error("Invalid CHANNELS_BRIDGE_PORT");
  const store = new BetaStore(config.statePath, config.projectId);
  const bridge = new BetaBridge(store, new BetaApi(config), client);
  const outbound = createSendServer(secret, bridge), inbound = createBetaWebhook(config, store);
  let stopping = false, ticking = false;
  try {
    for (const [server, listenPort] of [[outbound, port], [inbound, config.webhookPort]] as const) {
      await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(listenPort, "127.0.0.1", resolve); });
    }
  } catch { outbound.close(); inbound.close(); store.close(); throw new Error("Beta bridge ports unavailable"); }
  const drain = setInterval(() => { if (!stopping) void bridge.drain(config.projectId, config.agentHandle).catch(() => console.warn("Photon queue storage unavailable")); }, 1000);
  const tick = setInterval(async () => {
    if (stopping || ticking) return; ticking = true;
    try { await client.post("/internal/channels/tick", {}); } catch { console.warn("Channels tick unavailable"); } finally { ticking = false; }
  }, 60_000);
  const stop = async () => {
    if (stopping) return; stopping = true; clearInterval(drain); clearInterval(tick);
    outbound.close(); inbound.close(); outbound.closeAllConnections(); inbound.closeAllConnections();
    // Let already reserved bounded work settle; never clear uncertain rows.
    while (bridge.busy() || ticking) await new Promise(resolve => setTimeout(resolve, 100));
    store.close();
  };
  process.once("SIGINT", () => { void stop(); }); process.once("SIGTERM", () => { void stop(); });
  console.log(`Photon Beta text/image bridge ready on loopback ports ${port}/${config.webhookPort}; public HTTPS webhook setup is separate.`);
  console.log("Persisted event states:", store.counts());
}
