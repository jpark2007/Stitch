/** A deliberately single-route tunnel target; the API authenticates the raw webhook. */
import { createServer, request } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { Socket } from "node:net";
import { pathToFileURL } from "node:url";

export const CALL_WEBHOOK_PATH = "/internal/channels/elevenlabs/post-call";
const BODY_LIMIT = 256 * 1024;
const RESPONSE_LIMIT = 4096;

export function callProxyConfig(env: NodeJS.ProcessEnv = process.env) {
  if (env.STITCH_CHANNELS_CALL_PROXY !== "1") throw new Error("STITCH_CHANNELS_CALL_PROXY=1 is required");
  const upstream = new URL(env.CHANNELS_CALL_PROXY_UPSTREAM ?? "http://127.0.0.1:8011");
  if (upstream.protocol !== "http:" || !["127.0.0.1", "[::1]"].includes(upstream.hostname)
      || upstream.username || upstream.password || upstream.pathname !== "/" || upstream.search || upstream.hash) {
    throw new Error("Call proxy upstream must be a numeric loopback HTTP origin");
  }
  const port = Number(env.CHANNELS_CALL_PROXY_PORT ?? "3023");
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid call proxy port");
  return { upstream, port };
}

function reply(res: ServerResponse, status: number): void {
  if (res.destroyed || res.writableEnded) return;
  // Never relay provider/API response bodies, cookies, redirects or diagnostics.
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", Connection: "close" });
  res.end(JSON.stringify({ status }));
}

function readBody(req: IncomingMessage, timeoutMs: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks: Buffer[] = [];
    const finish = (status?: number) => {
      clearTimeout(timer);
      req.off("data", data); req.off("end", end); req.off("error", error); req.off("aborted", aborted);
      if (status) { req.pause(); reject(status); } else resolve(Buffer.concat(chunks, size));
    };
    const data = (chunk: Buffer) => {
      size += chunk.length;
      if (size > BODY_LIMIT) finish(413); else chunks.push(chunk);
    };
    const end = () => finish();
    const error = () => finish(400);
    const aborted = () => finish(400);
    const timer = setTimeout(() => finish(408), timeoutMs);
    req.on("data", data); req.once("end", end); req.once("error", error); req.once("aborted", aborted);
  });
}

function forward(upstream: URL, body: Buffer, signature: string, contentType: string,
                 timeoutMs: number, signal: AbortSignal): Promise<number> {
  return new Promise(resolve => {
    let settled = false;
    const finish = (status: number) => {
      if (settled) return;
      settled = true; clearTimeout(timer); resolve(status);
    };
    // node:http never follows redirects. No inbound authorization/cookie/host headers pass through.
    const outgoing = request(new URL(CALL_WEBHOOK_PATH, upstream), {
      method: "POST", signal, agent: false,
      headers: { "Content-Type": contentType, "ElevenLabs-Signature": signature, "Content-Length": body.length },
    }, incoming => {
      let size = 0;
      incoming.on("data", (chunk: Buffer) => {
        size += chunk.length;
        if (size > RESPONSE_LIMIT) { finish(502); outgoing.destroy(); incoming.destroy(); }
      });
      incoming.on("end", () => finish(incoming.statusCode ?? 502));
      incoming.on("error", () => finish(502));
      incoming.on("aborted", () => finish(502));
    });
    const timer = setTimeout(() => { finish(504); outgoing.destroy(); }, timeoutMs);
    outgoing.on("error", () => finish(502));
    outgoing.end(body);
  });
}

/** Importing starts nothing. Deadline overrides are for offline tests, never environment-controlled. */
export function createCallWebhookProxy(env: NodeJS.ProcessEnv = process.env,
  deadlines: { readMs?: number; upstreamMs?: number } = {}) {
  const { upstream } = callProxyConfig(env);
  const readMs = deadlines.readMs ?? 2000, upstreamMs = deadlines.upstreamMs ?? 5000;
  if (![readMs, upstreamMs].every(n => Number.isFinite(n) && n > 0)
      || readMs > 2000 || upstreamMs > 5000) throw new Error("Invalid call proxy deadlines");
  let active = 0;
  const server = createServer({ maxHeaderSize: 8192, connectionsCheckingInterval: 250 }, async (req, res) => {
    req.on("error", () => {}); // Client disconnects must not become uncaught stream errors.
    // Compare the original request target: no query, normalization, alternate path or method.
    if (req.url !== CALL_WEBHOOK_PATH) { reply(res, 404); return; }
    if (req.method !== "POST") { reply(res, 405); return; }
    const length = req.headers["content-length"];
    if (length !== undefined && (!/^\d+$/.test(length) || Number(length) > BODY_LIMIT)) {
      reply(res, 413); return;
    }
    const signatures = req.headersDistinct["elevenlabs-signature"];
    if (!signatures || signatures.length !== 1 || !signatures[0]) { reply(res, 401); return; }
    if (active >= 4) { reply(res, 503); return; }
    active++;
    const controller = new AbortController();
    const cancel = () => controller.abort();
    res.once("close", cancel);
    try {
      const body = await readBody(req, readMs);
      if (!res.destroyed) reply(res, await forward(upstream, body, signatures[0],
        req.headers["content-type"] ?? "application/json", upstreamMs, controller.signal));
    } catch (status) { reply(res, typeof status === "number" ? status : 400); }
    finally { active--; res.off("close", cancel); }
  });
  // Slow headers have their own bound; body deadline is absolute, not an idle timeout.
  server.headersTimeout = 2000;
  server.requestTimeout = 7000;
  server.maxConnections = 32;
  server.on("checkContinue", (_req, res) => reply(res, 417));
  server.on("checkExpectation", (_req, res) => reply(res, 417));
  server.on("clientError", (_error, socket) => {
    if (!(socket instanceof Socket) || socket.bytesWritten > 0 || !socket.writable) { socket.destroy(); return; }
    socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const config = callProxyConfig();
    const server = createCallWebhookProxy();
    server.on("error", () => { console.error("Call webhook proxy could not listen"); process.exitCode = 1; });
    server.listen(config.port, "127.0.0.1", () => console.log(`Call webhook proxy listening on 127.0.0.1:${config.port}`));
    const stop = () => { server.close(); server.closeAllConnections(); };
    process.once("SIGINT", stop); process.once("SIGTERM", stop);
  } catch { console.error("Call webhook proxy configuration invalid or disabled"); process.exitCode = 1; }
}
