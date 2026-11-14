import assert from "node:assert/strict";
import { test } from "node:test";
import { once } from "node:events";
import { createServer } from "node:http";
import { ApiClient, TerminalOutbox, createSendServer, localApiUrl } from "../src/bridge.ts";
import { Authenticator, MAX_BODY_BYTES, parseMessage, signature, signedHeaders, validateSecret } from "../src/protocol.ts";

const secret = "0123456789abcdef0123456789abcdef";
const now = 1_700_000_000_000;
const nonce = "12345678-1234-1234-1234-123456789abc";
const inbound = { messageId: "message-1", provider: "terminal" as const, spaceId: "chat-1", senderId: "person-1", text: "A childhood memory" };
const outbound = { messageId: "reply-1", provider: "terminal" as const, spaceId: "chat-1", text: "Receipt recorded" };

test("shared secrets reject blank, edge whitespace and invalid Unicode without normalizing", () => {
  const invalid = ["", "short", "x".repeat(31), "é".repeat(15) + "x", " ".repeat(32), "\t".repeat(40), "\n".repeat(40), "\u2003".repeat(32),
    " " + secret, secret + " ", "\t" + secret, secret + "\n", secret + "\r\n", "\u00a0" + secret,
    "\uFEFF" + secret, secret + "\uFEFF", "\u0085" + secret, secret + "\u001C", secret + "\uD800"];
  for (const value of invalid) {
    assert.throws(() => validateSecret(value), { message: "CHANNELS_SHARED_SECRET requires at least 32 UTF-8 bytes and no edge whitespace" });
    assert.throws(() => new Authenticator(value));
  }
  for (const value of ["x".repeat(32), "é".repeat(16), "x".repeat(16) + " " + "y".repeat(16)]) {
    assert.equal(validateSecret(value), value);
    const raw = Buffer.from("{}");
    new Authenticator(value).verify("POST", "/send", new Headers(signedHeaders(value, "/send", raw)), raw);
  }
});

test("HMAC known vector covers method, exact path and raw bytes", () => {
  const raw = Buffer.from("{}");
  assert.equal(signature(secret, "POST", "/internal/channels/tick", "1700000000", nonce, raw), "d39ae3bc8c276b08da2e389c7c86ec995064d4e126df1ba1f6dcaccd0c86c6bd");
  const headers = new Headers(signedHeaders(secret, "/send", raw, now));
  for (const [method, path, body] of [["GET", "/send", raw], ["POST", "/else", raw], ["POST", "/send", Buffer.from("{ }")]] as const) {
    assert.throws(() => new Authenticator(secret).verify(method, path, headers, body, now), /authentication/);
  }
  assert.throws(() => new Authenticator("short"), /32/);
});

test("replay guard rejects stale/future, duplicate and noncanonical headers, fails closed at capacity", () => {
  const raw = Buffer.from("{}");
  const auth = new Authenticator(secret, 1);
  const headers = new Headers(signedHeaders(secret, "/send", raw, now));
  auth.verify("POST", "/send", headers, raw, now);
  assert.throws(() => auth.verify("POST", "/send", headers, raw, now), /Replayed/);
  assert.throws(() => auth.verify("POST", "/send", new Headers(signedHeaders(secret, "/send", raw, now)), raw, now), /capacity/);
  for (const offset of [-301_000, 301_000]) assert.throws(() => new Authenticator(secret).verify("POST", "/send", headers, raw, now + offset), /authentication/);
  headers.set("x-channels-nonce", "NOT-A-UUID");
  assert.throws(() => new Authenticator(secret).verify("POST", "/send", headers, raw, now), /authentication/);
});

test("future timestamps remain replay-protected through their entire acceptance window", () => {
  const raw = Buffer.from("{}");
  const auth = new Authenticator(secret);
  const headers = new Headers(signedHeaders(secret, "/send", raw, now + 300_000));
  auth.verify("POST", "/send", headers, raw, now);
  assert.throws(() => auth.verify("POST", "/send", headers, raw, now + 301_000), /Replayed/);
});

test("message validation enforces strict shape and UTF-8 byte bounds", () => {
  assert.deepEqual(parseMessage(inbound, true), inbound);
  for (const change of [{ text: " " }, { text: "é".repeat(4001) }, { senderId: "é".repeat(129) }, { provider: "sms" }, { familyId: "spoof" }]) {
    assert.throws(() => parseMessage({ ...inbound, ...change }, true), /Invalid/);
  }
  assert.equal(parseMessage({ ...inbound, text: "é".repeat(4000) }, true).text.length, 4000);
  assert.throws(() => parseMessage({ ...outbound, senderId: "extra" }, false), /Invalid/);
  for (const text of ["\u0085", "\uFEFF", "\u001C", "\uD800", "\uDC00"]) assert.throws(() => parseMessage({ ...inbound, text }, true), /Invalid/);
  assert.equal(parseMessage({ ...inbound, text: "😀" }, true).text, "😀");
});

test("API target is loopback origin only; credentials and path overrides rejected", () => {
  assert.equal(localApiUrl("http://localhost:8000").host, "localhost:8000");
  for (const url of ["https://example.com", "http://user:pass@localhost", "http://localhost/prefix", "http://localhost?secret=x", "http://localhost#x"]) assert.throws(() => localApiUrl(url));
});

test("API retries transient responses with a new nonce and unchanged message identity", async () => {
  const requests: RequestInit[] = [];
  const client = new ApiClient("http://localhost:8000", secret, {
    fetch: (async (_url, init) => { requests.push(init!); return requests.length === 1 ? new Response("{}", { status: 503 }) : Response.json({ status: "queued" }, { status: 202 }); }) as typeof fetch,
    delay: async () => {},
  });
  const receipt = await client.post("/internal/channels/inbound", inbound);
  assert.equal(receipt.status, 202);
  assert.equal(requests.length, 2);
  assert.equal(Buffer.from(requests[0].body as Uint8Array).toString(), Buffer.from(requests[1].body as Uint8Array).toString());
  const first = new Headers(requests[0].headers), second = new Headers(requests[1].headers);
  assert.notEqual(first.get("x-channels-nonce"), second.get("x-channels-nonce"));
  for (const request of requests) new Authenticator(secret).verify("POST", "/internal/channels/inbound", new Headers(request.headers), request.body as Uint8Array);
});

test("API surfaces permanent errors without response-body leakage or retry", async () => {
  let calls = 0;
  const client = new ApiClient("http://localhost:8000", secret, { fetch: (async () => { calls++; return new Response("private detail", { status: 403 }); }) as typeof fetch });
  await assert.rejects(client.post("/internal/channels/inbound", inbound), { message: "API rejected request (403)" });
  assert.equal(calls, 1);
});

test("actual HTTP API timeout and redirect fail without following redirect", async (t) => {
  let redirected = 0;
  const server = createServer((req, res) => {
    if (req.url === "/internal/channels/tick") { res.writeHead(302, { location: "/stolen" }); res.end(); }
    else if (req.url === "/stolen") { redirected++; res.end("{}"); }
  });
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  t.after(() => { server.closeAllConnections(); server.close(); });
  const address = server.address(); assert(address && typeof address !== "string");
  const client = new ApiClient(`http://127.0.0.1:${address.port}`, secret, { attempts: 1, timeoutMs: 30 });
  await assert.rejects(client.post("/internal/channels/tick", {}), /unreachable|timed out/);
  await assert.rejects(client.post("/internal/channels/inbound", inbound), /unreachable|timed out/);
  assert.equal(redirected, 0);
});

test("oversized API response fails boundedly", async () => {
  const client = new ApiClient("http://localhost:8000", secret, { fetch: (async () => new Response("x".repeat(MAX_BODY_BYTES + 1))) as typeof fetch });
  await assert.rejects(client.post("/internal/channels/tick", {}), /response too large/);
});

test("outbox deduplicates concurrent delivery and refuses changed content, unknown spaces, cloud sends", async () => {
  const outbox = new TerminalOutbox();
  let sends = 0;
  outbox.register("chat-1", async () => { sends++; await new Promise((resolve) => setTimeout(resolve, 10)); });
  await Promise.all([outbox.send(outbound), outbox.send(outbound)]);
  await outbox.send(outbound);
  await outbox.send({ text: outbound.text, spaceId: outbound.spaceId, provider: outbound.provider, messageId: outbound.messageId });
  assert.equal(sends, 1);
  await assert.rejects(outbox.send({ ...outbound, text: "changed" }), /different content/);
  await assert.rejects(outbox.send({ ...outbound, messageId: "other", spaceId: "unknown" }), /Unknown/);
  await assert.rejects(outbox.send({ ...outbound, provider: "imessage" }), /Only terminal/);
});

test("uncertain delivery is bounded and not retried", async () => {
  const outbox = new TerminalOutbox(10);
  let sends = 0;
  outbox.register("chat-1", async () => { sends++; await new Promise(() => {}); });
  await assert.rejects(outbox.send(outbound), /uncertain/);
  await assert.rejects(outbox.send(outbound), /uncertain/);
  assert.equal(sends, 1);
});

test("real /send HTTP server authenticates raw body and rejects replay, bad schema, oversize and routes", async (t) => {
  const outbox = new TerminalOutbox();
  const sent: string[] = [];
  outbox.register("chat-1", async (text) => { sent.push(text); });
  const server = createSendServer(secret, outbox);
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  t.after(() => { server.closeAllConnections(); server.close(); });
  const address = server.address(); assert(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  const body = Buffer.from(JSON.stringify(outbound));
  const headers = signedHeaders(secret, "/send", body);
  const first = await fetch(`${origin}/send`, { method: "POST", headers, body });
  assert.equal(first.status, 200);
  assert.equal((await fetch(`${origin}/send`, { method: "POST", headers, body })).status, 409);
  assert.equal((await fetch(`${origin}/send`, { method: "POST", headers: signedHeaders(secret, "/send", body), body })).status, 200);
  assert.deepEqual(sent, [outbound.text]);
  const invalid = Buffer.from(JSON.stringify({ ...outbound, extra: true }));
  assert.equal((await fetch(`${origin}/send`, { method: "POST", headers: signedHeaders(secret, "/send", invalid), body: invalid })).status, 422);
  assert.equal((await fetch(`${origin}/send`, { method: "POST", headers: { "content-type": "application/json" }, body })).status, 401);
  const large = Buffer.from("x".repeat(MAX_BODY_BYTES + 1));
  assert.equal((await fetch(`${origin}/send`, { method: "POST", headers: signedHeaders(secret, "/send", large), body: large })).status, 413);
  assert.equal((await fetch(`${origin}/send?x=1`, { method: "POST", headers, body })).status, 404);
});
