import assert from "node:assert/strict";
import { createServer, request } from "node:http";
import type { Server, IncomingMessage, ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { test } from "node:test";
import { CALL_WEBHOOK_PATH, callProxyConfig, createCallWebhookProxy } from "../src/call-webhook-proxy.ts";

const enabled = { STITCH_CHANNELS_CALL_PROXY: "1" };
const signature = "t=123,v0=0123456789abcdef";
async function listen(server: Server): Promise<string> {
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}
async function stop(server: Server) {
  server.closeAllConnections();
  await new Promise<void>(resolve => server.close(() => resolve()));
}
async function fixture(handler: (req: IncomingMessage, res: ServerResponse) => void,
  deadlines = { readMs: 100, upstreamMs: 200 }) {
  const upstream = createServer(handler);
  const origin = await listen(upstream);
  const proxy = createCallWebhookProxy({ ...enabled, CHANNELS_CALL_PROXY_UPSTREAM: origin }, deadlines);
  const url = await listen(proxy);
  return { url, close: async () => { await stop(proxy); await stop(upstream); } };
}
function send(origin: string, { path = CALL_WEBHOOK_PATH, method = "POST", body = Buffer.from("{}"),
  headers = {} as Record<string, string>, slow = false } = {}) {
  return new Promise<{ status: number; body: string; headers: IncomingMessage["headers"] }>((resolve, reject) => {
    const req = request(origin + path, { method, agent: false,
      headers: { "ElevenLabs-Signature": signature, ...(method === "GET" ? { "Content-Length": String(body.length) } : {}), ...headers } }, res => {
      const chunks: Buffer[] = [];
      res.on("data", chunk => chunks.push(chunk));
      res.on("end", () => { req.destroy(); resolve({ status: res.statusCode!, body: Buffer.concat(chunks).toString(), headers: res.headers }); });
    });
    req.on("error", reject);
    if (slow) { req.flushHeaders(); req.write(body); } else req.end(body);
  });
}

test("configuration is explicit, numeric-loopback only, with no alternate target", () => {
  for (const value of [undefined, "true", "0"]) assert.throws(() => callProxyConfig({ STITCH_CHANNELS_CALL_PROXY: value }));
  assert.equal(callProxyConfig(enabled).port, 3023);
  for (const origin of ["http://localhost:8011", "https://127.0.0.1", "http://example.com", "http://127.0.0.1/x",
    "http://user:secret@127.0.0.1", "http://127.0.0.1/?x=1", "http://127.0.0.1/#x"]) {
    assert.throws(() => callProxyConfig({ ...enabled, CHANNELS_CALL_PROXY_UPSTREAM: origin }));
  }
  assert.equal(callProxyConfig({ ...enabled, CHANNELS_CALL_PROXY_UPSTREAM: "http://[::1]:8011" }).upstream.hostname, "[::1]");
});

test("preserves exact Chinese bytes/signature; forwards no credentials and sanitizes response", async () => {
  const body = Buffer.from(' {"text":"你好，奶奶！"}\n');
  let seen = false;
  const f = await fixture((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      assert.deepEqual(Buffer.concat(chunks), body);
      assert.equal(req.url, CALL_WEBHOOK_PATH);
      assert.equal(req.headers["elevenlabs-signature"], signature);
      assert.equal(req.headers["content-type"], "application/json; charset=utf-8");
      assert.equal(req.headers.authorization, undefined);
      assert.equal(req.headers.cookie, undefined);
      assert.equal(req.headers["x-secret"], undefined);
      seen = true;
      res.writeHead(200, { "Set-Cookie": "secret", Location: "https://example.com" }); res.end("PRIVATE TRANSCRIPT");
    });
  });
  try {
    const r = await send(f.url, { body, headers: { "Content-Type": "application/json; charset=utf-8",
      Authorization: "Bearer secret", Cookie: "session=secret", "X-Secret": "secret" } });
    assert.equal(r.status, 200); assert.equal(seen, true);
    assert.equal(r.body, '{"status":200}'); assert.equal(r.headers["set-cookie"], undefined);
    assert.equal(r.headers.location, undefined);
  } finally { await f.close(); }
});

test("wrong paths, query, method, absent signature and declared oversize never reach upstream", async () => {
  let count = 0;
  const f = await fixture((_req, res) => { count++; res.end(); });
  try {
    for (const path of ["/", "/docs", CALL_WEBHOOK_PATH + "?x=1", CALL_WEBHOOK_PATH + "/", "/internal/channels/%65levenlabs/post-call"]) {
      assert.equal((await send(f.url, { path })).status, 404);
    }
    assert.equal((await send(f.url, { method: "GET" })).status, 405);
    assert.equal((await send(f.url, { headers: { "ElevenLabs-Signature": "" } })).status, 401);
    assert.equal((await send(f.url, { headers: { "Content-Length": "262145" } })).status, 413);
    assert.equal(count, 0);
  } finally { await f.close(); }
});

test("chunked body cap is enforced and exact boundary is accepted", async () => {
  let count = 0;
  const f = await fixture((req, res) => { count++; req.resume(); req.on("end", () => res.end()); });
  try {
    assert.equal((await send(f.url, { body: Buffer.alloc(262145), headers: { "Transfer-Encoding": "chunked" } })).status, 413);
    assert.equal(count, 0);
    assert.equal((await send(f.url, { body: Buffer.alloc(262144) })).status, 200);
    assert.equal(count, 1);
  } finally { await f.close(); }
});

test("four slow bodies fill slots, fifth fails fast, deadline releases slots", async () => {
  let count = 0;
  const f = await fixture((_req, res) => { count++; res.end(); }, { readMs: 300, upstreamMs: 200 });
  try {
    const held = Array.from({ length: 4 }, () => send(f.url, { slow: true, headers: { "Transfer-Encoding": "chunked" } }));
    // Each request was flushed before the barrier request; localhost acceptance preserves this queue.
    await new Promise(resolve => setTimeout(resolve, 40));
    assert.equal((await send(f.url)).status, 503);
    assert.deepEqual((await Promise.all(held)).map(r => r.status), [408, 408, 408, 408]);
    assert.equal(count, 0);
    assert.equal((await send(f.url)).status, 200);
  } finally { await f.close(); }
});

test("upstream status is preserved, redirects not followed, errors never exposed", async () => {
  for (const status of [201, 204, 302, 400, 401, 403, 404, 429, 500, 503]) {
    let count = 0;
    const f = await fixture((_req, res) => { count++; res.writeHead(status, { Location: "/secret" }); res.end("SECRET"); });
    try {
      const r = await send(f.url);
      assert.equal(r.status, status); assert.equal(count, 1);
      assert.equal(r.body.includes("SECRET"), false); assert.equal(r.headers.location, undefined);
    } finally { await f.close(); }
  }
});

test("upstream timeout, disconnected response and large response return bounded failures", async () => {
  for (const [handler, expected] of [
    [(_req: IncomingMessage, _res: ServerResponse) => {}, 504],
    [(_req: IncomingMessage, res: ServerResponse) => res.destroy(), 502],
    [(_req: IncomingMessage, res: ServerResponse) => res.end("s".repeat(4097)), 502],
  ] as const) {
    const f = await fixture(handler);
    try { const r = await send(f.url); assert.equal(r.status, expected); assert.ok(r.body.length < 100); }
    finally { await f.close(); }
  }
});

test("body deadline is absolute even while bytes trickle in", async () => {
  let count = 0;
  const f = await fixture((_req, res) => { count++; res.end(); });
  try {
    const status = await new Promise<number>((resolve, reject) => {
      const req = request(f.url + CALL_WEBHOOK_PATH, { method: "POST", headers: {
        "ElevenLabs-Signature": signature, "Transfer-Encoding": "chunked",
      } }, res => { res.resume(); res.on("end", () => { clearInterval(trickle); req.destroy(); resolve(res.statusCode!); }); });
      req.on("error", reject);
      const trickle = setInterval(() => req.write("x"), 15);
      req.on("close", () => clearInterval(trickle)); req.flushHeaders();
    });
    assert.equal(status, 408); assert.equal(count, 0);
  } finally { await f.close(); }
});

test("duplicate signatures and Expect are refused without forwarding", async () => {
  let count = 0;
  const f = await fixture((_req, res) => { count++; res.end(); });
  try {
    const status = await new Promise<number>((resolve, reject) => {
      const req = request(f.url + CALL_WEBHOOK_PATH, { method: "POST", headers: {
        "ElevenLabs-Signature": [signature, "other"],
      } }, res => { res.resume(); res.on("end", () => resolve(res.statusCode!)); });
      req.on("error", reject); req.end("{}");
    });
    assert.equal(status, 401);
    assert.equal((await send(f.url, { headers: { Expect: "100-continue" } })).status, 417);
    assert.equal(count, 0);
  } finally { await f.close(); }
});

test("slots remain occupied through upstream work and recover after timeout", async () => {
  let count = 0;
  let allStarted!: () => void;
  const ready = new Promise<void>(resolve => { allStarted = resolve; });
  const f = await fixture((_req, res) => { count++; if (count === 4) allStarted(); if (count > 4) res.end(); },
    { readMs: 100, upstreamMs: 300 });
  try {
    const held = Array.from({ length: 4 }, () => send(f.url));
    await ready;
    assert.equal((await send(f.url)).status, 503);
    assert.deepEqual((await Promise.all(held)).map(r => r.status), [504, 504, 504, 504]);
    assert.equal((await send(f.url)).status, 200);
  } finally { await f.close(); }
});
