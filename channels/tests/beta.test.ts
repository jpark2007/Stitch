import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:http";
import { connect } from "node:net";
import { DatabaseSync } from "node:sqlite";
import { Webhook } from "standardwebhooks";
import { betaConfig, BetaApi, BetaBridge, createBetaWebhook, parseBetaEvent, type BetaConfig } from "../src/beta.ts";
import { BetaStore, EVENT_RETENTION_MS, ROUTE_RETENTION_MS, MAX_EVENT_ATTEMPTS } from "../src/beta-store.ts";
import { ApiClient, createSendServer } from "../src/bridge.ts";
import { Authenticator, signedHeaders, ProtocolError } from "../src/protocol.ts";

const ident = (prefix: string, n = 1) => `pho_${prefix}_${String(n).padStart(26, "0")}`;
const projectId = ident("prj"), agentHandle = "+15555550123";
const secret = "whsec_" + Buffer.alloc(32, 8).toString("base64");
const config: BetaConfig = { projectId, apiKey: "pho_sk_test", webhookSecret: secret, agentHandle, statePath: "unused", webhookPort: 3012 };
const human = { id: ident("usr"), projectId, platform: "imessage", kind: "human", handle: "Nate@Example.COM" };
const agent = { ...human, id: ident("usr", 2), kind: "agent", handle: agentHandle };
const dm = { id: ident("spc"), projectId, platform: "imessage", kind: "direct", participantIds: [human.id, agent.id] };
function event() { return { id: ident("evt"), apiVersion: "2026-07-01", type: "message.received", source: "imessage.photon", timestamp: new Date().toISOString(), data: { projectId, object: { id: ident("msg"), projectId, platform: "imessage", status: "received", sentBy: { ...human }, receivedBy: { ...agent }, space: { ...dm, participantIds: [...dm.participantIds] }, content: { type: "text", format: "plain", text: "Grandma taught me to bake." } as any } } }; }
function fixture(t: any, now: () => number = Date.now) {
  const dir = mkdtempSync(join(tmpdir(), "stitch-beta-")); let store = new BetaStore(join(dir, "state.sqlite"), projectId, now);
  t.after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });
  return { dir, get store() { return store; }, restart() { store.close(); store = new BetaStore(join(dir, "state.sqlite"), projectId, now); return store; } };
}
function apiFixture() {
  const calls: { url: string; init: RequestInit }[] = [];
  const image = Buffer.from("synthetic bytes validated by API, not bridge");
  const fetcher: typeof fetch = async (input, init = {}) => {
    const url = String(input); calls.push({ url, init });
    assert.equal(init.redirect, "error");
    assert.equal((init.headers as any).Authorization, "Bearer pho_sk_test");
    if (url.endsWith(`/spaces/${dm.id}`)) return Response.json(dm);
    if (url.endsWith(`/users/${human.id}`)) return Response.json(human);
    if (url.endsWith(`/users/${agent.id}`)) return Response.json(agent);
    if (url.endsWith("/content")) return new Response(image, { headers: { "content-type": "image/png" } });
    if (url.includes("/attachments/")) return Response.json({ id: ident("att"), projectId, contentType: "image/png", sizeBytes: image.length });
    if (url.endsWith("/messages")) return Response.json({ id: ident("msg", 2), projectId, platform: "imessage", status: "dispatched", space: dm, sentBy: agent }, { status: 202 });
    throw new Error("Unexpected URL");
  };
  return { calls, fetcher, api: new BetaApi(config, fetcher) };
}

test("Beta configuration explicitly selects version and intended agent; Stable credentials cannot activate it", () => {
  const env = { CHANNELS_PHOTON_VERSION: "beta", PHOTON_PROJECT_ID: projectId, PHOTON_API_KEY: "pho_sk_test", PHOTON_WEBHOOK_SECRET: secret, PHOTON_AGENT_HANDLE: agentHandle };
  assert.equal(betaConfig(env).projectId, projectId);
  for (const key of Object.keys(env)) assert.throws(() => betaConfig({ ...env, [key]: "" }));
  assert.throws(() => betaConfig({ SPECTRUM_PROJECT_ID: "old", SPECTRUM_PROJECT_SECRET: "old" }));
});

test("Beta contract binds header/event/project/version and every participant before queue effects", () => {
  assert.equal(parseBetaEvent(event(), projectId, ident("evt"), agentHandle).proof.handle, human.handle);
  const mutations = [
    (e: any) => { e.id = ident("evt", 2); }, (e: any) => { e.apiVersion = "future"; },
    (e: any) => { e.source = "email.photon"; }, (e: any) => { e.data.projectId = ident("prj", 2); },
    (e: any) => { e.data.object.projectId = ident("prj", 2); }, (e: any) => { e.data.object.status = "dispatched"; },
    (e: any) => { e.data.object.sentBy.projectId = ident("prj", 2); }, (e: any) => { e.data.object.receivedBy.kind = "human"; },
    (e: any) => { delete e.data.object.receivedBy; }, (e: any) => { e.data.object.receivedBy.handle = "+15555550124"; },
    (e: any) => { e.data.object.space.kind = "group"; }, (e: any) => { e.data.object.space.participantIds.push(ident("usr", 3)); },
    (e: any) => { e.data.object.space.projectId = ident("prj", 2); }, (e: any) => { e.data.object.content = { type: "voice", attachmentId: ident("att") }; },
    (e: any) => { e.data.object.content = { type: "group", parts: [{ type: "text", format: "plain", text: "LINK " + "a".repeat(43) }, { type: "attachment", attachmentId: ident("att") }] }; },
  ];
  for (const mutate of mutations) { const e = event(); mutate(e); assert.throws(() => parseBetaEvent(e, projectId, ident("evt"), agentHandle)); }
});

test("signed webhook persists before ACK, rejects tampering/stale/altered duplicates, survives restart", async t => {
  const f = fixture(t), server = createBetaWebhook(config, f.store);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve)); t.after(() => server.close());
  const url = `http://127.0.0.1:${(server.address() as any).port}/photon/webhook`;
  async function send(body = event(), timestamp = new Date(), tamper = false) {
    const raw = JSON.stringify(body), signature = new Webhook(secret).sign(body.id, timestamp, raw);
    return fetch(url, { method: "POST", body: tamper ? raw + " " : raw, headers: { "content-type": "application/json", "webhook-id": body.id, "webhook-timestamp": String(Math.floor(timestamp.getTime() / 1000)), "webhook-signature": signature } });
  }
  const original = event();
  assert.equal((await send(original)).status, 204);
  assert.equal((await send(original)).status, 204);
  const altered = structuredClone(original); altered.data.object.content.text = "altered";
  assert.equal((await send(altered)).status, 409);
  assert.equal((await send(original, new Date(), true)).status, 400);
  assert.equal((await send(original, new Date(Date.now() - 600_000))).status, 400);
  await new Promise<void>(resolve => server.close(() => resolve()));
  assert.equal(f.restart().next()!.id, original.id);
  assert.equal(f.restart().next(), undefined, "crashed processing cannot be blindly replayed");
});

test("group text and photo forward with deterministic IDs after consent; completed replay causes no side effects", async t => {
  const f = fixture(t), a = apiFixture(), posts: any[] = [];
  const client = { post: async (path: any, body: any) => { posts.push({ path, body }); return path.endsWith("authorize") ? { status: 200, body: { allowed: true } } : { status: 202, body: { accepted: true } }; } };
  const e = event(); e.data.object.content = { type: "group", parts: [e.data.object.content, { type: "attachment", attachmentId: ident("att") }] };
  const raw = JSON.stringify(e); f.store.accept(e.id, raw);
  await new BetaBridge(f.store, a.api, client).drain(projectId, agentHandle);
  assert.deepEqual(posts.map(p => p.path), ["/internal/channels/inbound", "/internal/channels/authorize", "/internal/channels/inbound-photo"]);
  assert.equal(posts[0].body.messageId, `${ident("msg")}:part:0`);
  assert.equal(posts[2].body.messageId, `${ident("msg")}:part:1`);
  assert.equal(posts[2].body.senderId, human.handle);
  assert.equal(f.restart().accept(e.id, raw), false);
  await new BetaBridge(f.store, a.api, client).drain(projectId, agentHandle);
  assert.equal(posts.length, 3);
});

test("LINK uses signed enrollment only; failed API effects block durable event rather than retry", async t => {
  const f = fixture(t), a = apiFixture(), posts: any[] = [];
  const e = event(); e.data.object.content.text = "LINK " + "a".repeat(43); f.store.accept(e.id, JSON.stringify(e));
  const client = { post: async (path: any, body: any) => { posts.push({ path, body }); throw new Error("lost ACK"); } };
  await new BetaBridge(f.store, a.api, client).drain(projectId, agentHandle);
  assert.equal(posts[0].path, "/internal/channels/enroll"); assert.equal(posts[0].body.spaceKind, "direct");
  await new BetaBridge(f.restart(), a.api, client).drain(projectId, agentHandle);
  assert.equal(posts.length, 1);
});

test("unknown consent prevents image download; changed direct space prevents any forwarding", async t => {
  const f = fixture(t), a = apiFixture();
  const e = event(); e.data.object.content = { type: "attachment", attachmentId: ident("att") }; f.store.accept(e.id, JSON.stringify(e));
  const client = { post: async () => { throw new Error("not opted in"); } };
  await new BetaBridge(f.store, a.api, client).drain(projectId, agentHandle);
  assert.ok(!a.calls.some(c => c.url.includes("attachments")));
  const next = event(); next.id = ident("evt", 2); f.store.accept(next.id, JSON.stringify(next));
  const badApi = new BetaApi(config, async () => Response.json({ ...dm, kind: "group" }));
  let called = false;
  await new BetaBridge(f.store, badApi, { post: async () => { called = true; return { status: 200, body: {} }; } }).drain(projectId, agentHandle);
  assert.equal(called, false);
});

test("outbound reserves before send, persists success and uncertainty across restarts", async t => {
  const f = fixture(t), a = apiFixture(), p = parseBetaEvent(event(), projectId, ident("evt"), agentHandle).proof;
  f.store.saveRoute(dm.id, p);
  const client = { post: async () => ({ status: 200, body: { allowed: true } }) };
  const message = { messageId: "reply-1", provider: "imessage" as const, spaceId: dm.id, text: "A follow-up" };
  await new BetaBridge(f.store, a.api, client).send(message);
  const reordered = { text: message.text, spaceId: message.spaceId, provider: message.provider, messageId: message.messageId };
  await new BetaBridge(f.store, a.api, client).send(reordered);
  await new BetaBridge(f.restart(), a.api, client).send(reordered);
  assert.equal(a.calls.filter(c => c.url.endsWith("/messages")).length, 1);
  const request = a.calls.find(c => c.url.endsWith("/messages"))!;
  assert.equal(JSON.parse(String(request.init.body)).senderId, agent.id);
  assert.match((request.init.headers as any)["Idempotency-Key"], /^stitch:/);
  await assert.rejects(new BetaBridge(f.store, a.api, client).send({ ...message, text: "changed" }));
  const failing = new BetaApi(config, async (url, init) => { if (String(url).endsWith("/messages")) throw new Error("lost ACK"); return a.fetcher(url, init); });
  await assert.rejects(new BetaBridge(f.store, failing, client).send({ ...message, messageId: "uncertain" }));
  await assert.rejects(new BetaBridge(f.restart(), a.api, client).send({ ...message, messageId: "uncertain" }), /uncertain/);
});

test("state is private, project-bound and holds an exclusive process lock", t => {
  const f = fixture(t);
  assert.throws(() => new BetaStore(join(f.dir, "state.sqlite"), projectId), /locked/);
  f.store.close();
  assert.throws(() => new BetaStore(join(f.dir, "state.sqlite"), ident("prj", 2)), /another Photon project/);
  f.restart();
  const other = mkdtempSync(join(tmpdir(), "stitch-public-")); t.after(() => rmSync(other, { recursive: true, force: true }));
  chmodSync(other, 0o755); assert.throws(() => new BetaStore(join(other, "state.sqlite"), projectId), /private/);
});

test("Beta REST rejects arbitrary URLs and bounds response bytes and total read time", async () => {
  const a = apiFixture();
  await assert.rejects(a.api.request("https://elsewhere.invalid/private"));
  assert.equal(a.calls.length, 0);
  const huge = new BetaApi(config, async () => new Response("x".repeat(262_145)));
  await assert.rejects(huge.request(`/spaces/${dm.id}`), /too large/);
  let cancelled = false;
  const stalled = new BetaApi(config, async () => new Response(new ReadableStream({ cancel() { cancelled = true; } })));
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20);
  await assert.rejects(stalled.request(`/spaces/${dm.id}`, { signal: controller.signal }), /timed out/);
  clearTimeout(timer); assert.equal(cancelled, true);
  const bytes = new BetaApi(config, async () => new Response(new Uint8Array(20_971_521), { headers: { "content-type": "image/png" } }));
  await assert.rejects(bytes.request(`/attachments/${ident("att")}/content`, { binary: true }), /20 MiB/);
});

test("webhook body bound and persistence failure never acknowledge work", async t => {
  const f = fixture(t), server = createBetaWebhook(config, f.store);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve)); t.after(() => server.close());
  const url = `http://127.0.0.1:${(server.address() as any).port}/photon/webhook`;
  assert.equal((await fetch(url, { method: "POST", body: "x".repeat(65_537), headers: { "content-type": "application/json" } })).status, 413);
  const e = event(), raw = JSON.stringify(e), now = new Date();
  f.store.accept = () => { throw new Error("disk full"); };
  assert.equal((await fetch(url, { method: "POST", body: raw, headers: { "content-type": "application/json", "webhook-id": e.id, "webhook-timestamp": String(Math.floor(now.getTime() / 1000)), "webhook-signature": new Webhook(secret).sign(e.id, now, raw) } })).status, 503);
  assert.equal(f.store.next(), undefined);
});

test("concurrent processing consumes one durable reservation and reply sends once", async t => {
  const f = fixture(t), a = apiFixture(), e = event(); f.store.accept(e.id, JSON.stringify(e));
  let posts = 0;
  const bridge = new BetaBridge(f.store, a.api, { post: async path => { posts++; return path.endsWith("authorize") ? { status: 200, body: { allowed: true } } : { status: 202, body: { accepted: true } }; } });
  await Promise.all([bridge.drain(projectId, agentHandle), bridge.drain(projectId, agentHandle)]);
  assert.equal(posts, 1);
  const reply = { messageId: "concurrent", provider: "imessage" as const, spaceId: dm.id, text: "Saved" };
  const results = await Promise.allSettled([bridge.send(reply), bridge.send(reply)]);
  assert.equal(results.filter(r => r.status === "fulfilled").length, 1);
  assert.equal(a.calls.filter(c => c.url.endsWith("/messages")).length, 1);
});

test("Beta transport uses actual signed local inbox and outbound HTTP contracts", async t => {
  const f = fixture(t), a = apiFixture(), localSecret = "local-test-shared-secret-".repeat(3);
  const auth = new Authenticator(localSecret), received: any[] = [];
  const apiServer = createServer(async (request, response) => {
    const chunks: Buffer[] = []; for await (const chunk of request) chunks.push(chunk);
    const raw = Buffer.concat(chunks), headers = new Headers();
    for (const [name, value] of Object.entries(request.headers)) if (typeof value === "string") headers.set(name, value);
    auth.verify("POST", request.url!, headers, raw);
    received.push({ path: request.url, value: JSON.parse(raw.toString()) });
    const authorization = request.url!.endsWith("authorize");
    response.writeHead(authorization ? 200 : 202, { "content-type": "application/json" }).end(JSON.stringify(authorization ? { allowed: true } : { accepted: true, duplicate: false }));
  });
  await new Promise<void>(resolve => apiServer.listen(0, "127.0.0.1", resolve)); t.after(() => apiServer.close());
  const client = new ApiClient(`http://127.0.0.1:${(apiServer.address() as any).port}`, localSecret, { attempts: 1 });
  const bridge = new BetaBridge(f.store, a.api, client), e = event();
  f.store.accept(e.id, JSON.stringify(e)); await bridge.drain(projectId, agentHandle);
  assert.equal(received[0].path, "/internal/channels/inbound");
  assert.equal(received[0].value.senderId, human.handle);
  const outbound = createSendServer(localSecret, bridge);
  await new Promise<void>(resolve => outbound.listen(0, "127.0.0.1", resolve)); t.after(() => outbound.close());
  const raw = Buffer.from(JSON.stringify({ messageId: "http-reply", provider: "imessage", spaceId: dm.id, text: "Tell me more" }));
  const response = await fetch(`http://127.0.0.1:${(outbound.address() as any).port}/send`, { method: "POST", headers: signedHeaders(localSecret, "/send", raw), body: raw });
  assert.equal(response.status, 200);
  const reordered = Buffer.from(JSON.stringify({ text: "Tell me more", spaceId: dm.id, provider: "imessage", messageId: "http-reply" }));
  const duplicate = await fetch(`http://127.0.0.1:${(outbound.address() as any).port}/send`, { method: "POST", headers: signedHeaders(localSecret, "/send", reordered), body: reordered });
  assert.equal(duplicate.status, 200);
  assert.equal(a.calls.filter(c => c.url.endsWith("/messages")).length, 1);
  assert.equal(received[1].path, "/internal/channels/authorize");
});

test("Beta binary download obeys caller abort immediately and cancels its reader", async () => {
  let cancelled = false;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(new Uint8Array([1, 2, 3])); },
    cancel() { cancelled = true; return new Promise<void>(() => {}); },
  });
  const api = new BetaApi(config, async () => new Response(stream, { headers: { "content-type": "image/png" } }));
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10);
  const started = performance.now();
  try { await assert.rejects(api.request(`/attachments/${ident("att")}/content`, { binary: true, signal: controller.signal }), /timed out/); }
  finally { clearTimeout(timer); }
  assert.ok(performance.now() - started < 500, "must use caller deadline, not the 15-second download timeout");
  assert.equal(cancelled, true);
});

test("Beta rejects unsupported binary MIME while cancelling the response body", async () => {
  let cancelled = false;
  const stream = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; return new Promise<void>(() => {}); } });
  const api = new BetaApi(config, async () => new Response(stream, { headers: { "content-type": "text/html" } }));
  const started = performance.now();
  await assert.rejects(api.request(`/attachments/${ident("att")}/content`, { binary: true }), /Unsupported photo/);
  assert.ok(performance.now() - started < 500);
  assert.equal(cancelled, true);
});

test("STOP survives transient verification and idempotent inbox failures with durable backoff", async t => {
  let now = Date.now();
  const f = fixture(t, () => now), a = apiFixture(), e = event(); e.data.object.content.text = "STOP";
  let failures = 1, posts = 0;
  const api = new BetaApi(config, async (url, init) => failures-- > 0 ? new Response("", { status: 500 }) : a.fetcher(url, init));
  const client = { post: async () => { posts++; if (posts === 1) throw new ProtocolError(503, "temporary"); return { status: 202, body: { accepted: true } }; } };
  const raw = JSON.stringify(e); f.store.accept(e.id, raw);
  await new BetaBridge(f.store, api, client).drain(projectId, agentHandle);
  assert.equal(posts, 0); assert.equal(f.store.next(), undefined);
  assert.equal(f.restart().accept(e.id, raw), false);
  now += 1000;
  await new BetaBridge(f.store, api, client).drain(projectId, agentHandle);
  assert.equal(posts, 1); assert.equal(f.store.next(), undefined);
  now += 2000;
  await new BetaBridge(f.restart(), api, client).drain(projectId, agentHandle);
  assert.equal(posts, 2);
  assert.equal(f.store.counts().find(row => row.state === "done")?.count, 1);
});

test("retry exhaustion remains blocked and deduped across restart", async t => {
  let now = Date.now(); const f = fixture(t, () => now), e = event(); e.data.object.content.text = "STOP";
  f.store.accept(e.id, JSON.stringify(e)); let calls = 0;
  const api = new BetaApi(config, async () => { calls++; throw new TypeError("network unavailable"); });
  const client = { post: async () => { throw new Error("must not forward"); } };
  for (let i = 0; i < MAX_EVENT_ATTEMPTS + 2; i++) {
    await new BetaBridge(f.store, api, client).drain(projectId, agentHandle); now += 60_000;
  }
  assert.equal(calls, MAX_EVENT_ATTEMPTS);
  assert.equal(f.restart().next(), undefined);
  assert.equal(f.store.counts().find(row => row.state === "blocked")?.count, 1);
});

test("completed parts are checkpointed before retrying a later idempotent text", async t => {
  let now = Date.now(); const f = fixture(t, () => now), a = apiFixture(), e = event();
  e.data.object.content = { type: "group", parts: [{ type: "attachment", attachmentId: ident("att") }, { type: "text", format: "plain", text: "STOP" }] };
  f.store.accept(e.id, JSON.stringify(e)); const calls: string[] = []; let textFailures = 1;
  const client = { post: async (path: any) => { calls.push(path); if (path.endsWith("authorize")) return { status: 200, body: { allowed: true } };
    if (path.endsWith("/inbound") && textFailures-- > 0) throw new ProtocolError(502, "lost ACK");
    return { status: 202, body: { accepted: true } }; } };
  await new BetaBridge(f.store, a.api, client).drain(projectId, agentHandle);
  now += 1000;
  await new BetaBridge(f.restart(), a.api, client).drain(projectId, agentHandle);
  assert.equal(calls.filter(path => path.endsWith("inbound-photo")).length, 1);
  assert.equal(calls.filter(path => path.endsWith("/inbound")).length, 2);
  assert.equal(f.store.counts().find(row => row.state === "done")?.count, 1);
});

test("full route cache cannot prevent STOP or LINK from completing", async t => {
  const f = fixture(t), a = apiFixture();
  for (let i = 0; i < 1000; i++) f.store.saveRoute(`old-${i}`, { synthetic: i });
  const paths: string[] = [];
  const client = { post: async (path: any) => { paths.push(path); return path.endsWith("enroll") ? { status: 200, body: { status: "active" } } : { status: 202, body: { accepted: true } }; } };
  for (const [i, text] of ["STOP", "LINK " + "a".repeat(43)].entries()) {
    const e = event(); e.id = ident("evt", i + 1); e.data.object.content.text = text;
    f.store.accept(e.id, JSON.stringify(e)); await new BetaBridge(f.store, a.api, client).drain(projectId, agentHandle);
  }
  assert.deepEqual(paths, ["/internal/channels/inbound", "/internal/channels/enroll"]);
  assert.equal(f.store.counts().find(row => row.state === "done")?.count, 2);
  assert.equal(f.store.route(dm.id), undefined);
});

test("event capacity counts active work; done hashes survive retry horizon then prune", t => {
  let now = Date.now(); const f = fixture(t, () => now); f.store.close();
  const db = new DatabaseSync(join(f.dir, "state.sqlite"));
  db.exec("BEGIN"); const insert = db.prepare("INSERT INTO events (id,hash,body,state,created,updated) VALUES (?,?,'','done',?,?)");
  for (let i = 0; i < 10_000; i++) insert.run(`old-${i}`, "hash", now, now);
  db.exec("COMMIT"); db.close(); f.restart();
  assert.equal(f.store.accept("fresh", "synthetic"), true);
  assert.equal(f.store.counts().find(row => row.state === "done")?.count, 10_000);
  now += EVENT_RETENTION_MS - 1; f.store.prune();
  assert.equal(f.store.counts().find(row => row.state === "done")?.count, 10_000);
  now += 2; f.store.prune();
  assert.equal(f.store.counts().some(row => row.state === "done"), false);
  assert.equal(f.store.next(), undefined, "expired queued bodies cannot run");
  assert.equal(f.store.accept("fresh", "synthetic"), false, "uncertainty identity retained");
});

test("active queue still fails closed at capacity, without deleting uncertain rows", t => {
  const f = fixture(t); f.store.close(); const db = new DatabaseSync(join(f.dir, "state.sqlite"));
  db.exec("BEGIN"); const insert = db.prepare("INSERT INTO events (id,hash,body,state,created,updated) VALUES (?,?,'','blocked',?,?)");
  for (let i = 0; i < 10_000; i++) insert.run(`uncertain-${i}`, "hash", Date.now(), Date.now());
  db.exec("COMMIT"); db.close(); f.restart();
  assert.throws(() => f.store.accept("new", "STOP"), /maintenance/);
  assert.equal(f.store.counts().find(row => row.state === "blocked")?.count, 10_000);
});

test("legacy ledger migration preserves crashed processing and reply uncertainty", t => {
  const dir = mkdtempSync(join(tmpdir(), "stitch-beta-legacy-")), path = join(dir, "state.sqlite");
  const db = new DatabaseSync(path); chmodSync(path, 0o600);
  db.exec("CREATE TABLE config (project TEXT PRIMARY KEY); CREATE TABLE events (id TEXT PRIMARY KEY,hash TEXT NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL); CREATE TABLE replies (id TEXT PRIMARY KEY,hash TEXT NOT NULL,state TEXT NOT NULL); CREATE TABLE routes (id TEXT PRIMARY KEY,proof TEXT NOT NULL);");
  db.prepare("INSERT INTO config VALUES (?)").run(projectId);
  db.exec("INSERT INTO events VALUES ('old','hash','private','processing'); INSERT INTO replies VALUES ('reply','hash','sending'); INSERT INTO routes VALUES ('route','{}');"); db.close();
  const store = new BetaStore(path, projectId); t.after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });
  assert.equal(store.next(), undefined);
  assert.throws(() => store.reserveReply("reply", "hash"), /uncertain/);
  assert.deepEqual(store.route("route"), {});
  assert.equal(store.accept("new", "synthetic"), true);
});

test("403/404 remove refused payloads immediately; blocked payloads and routes expire", async t => {
  let now = Date.now(); const f = fixture(t, () => now), a = apiFixture();
  for (const [i, status] of [403, 404, 422].entries()) {
    const e = event(); e.id = ident("evt", i + 1); f.store.accept(e.id, JSON.stringify(e));
    await new BetaBridge(f.store, a.api, { post: async () => { throw new ProtocolError(status, "refused"); } }).drain(projectId, agentHandle);
  }
  f.store.saveRoute(dm.id, parseBetaEvent(event(), projectId, ident("evt"), agentHandle).proof);
  f.store.close(); let db = new DatabaseSync(join(f.dir, "state.sqlite"));
  assert.equal(db.prepare("SELECT count(*) AS n FROM events WHERE body!=''").get()!.n, 1); db.close(); f.restart();
  now += ROUTE_RETENTION_MS + 1; f.store.prune(); assert.equal(f.store.route(dm.id), undefined);
  f.store.close(); db = new DatabaseSync(join(f.dir, "state.sqlite"));
  assert.equal(db.prepare("SELECT body,state FROM events").get()!.body, "");
  assert.equal(db.prepare("SELECT state FROM events").get()!.state, "blocked"); db.close();
});

test("consent refusal before outbound send leaves no uncertain reservation", async t => {
  const f = fixture(t), a = apiFixture(); f.store.saveRoute(dm.id, parseBetaEvent(event(), projectId, ident("evt"), agentHandle).proof);
  const message = { messageId: "before-send", provider: "imessage" as const, spaceId: dm.id, text: "Question" };
  await assert.rejects(new BetaBridge(f.store, a.api, { post: async () => { throw new ProtocolError(403, "stopped"); } }).send(message));
  assert.equal(a.calls.some(call => call.url.endsWith("/messages")), false);
  await new BetaBridge(f.store, a.api, { post: async () => ({ status: 200, body: { allowed: true } }) }).send(message);
  assert.equal(a.calls.filter(call => call.url.endsWith("/messages")).length, 1);
});

test("webhook secret enforces 24 decoded bytes and rejects noncanonical base64", () => {
  const env = { CHANNELS_PHOTON_VERSION: "beta", PHOTON_PROJECT_ID: projectId, PHOTON_API_KEY: "pho_sk_test", PHOTON_AGENT_HANDLE: agentHandle };
  for (const bytes of [1, 23]) assert.throws(() => betaConfig({ ...env, PHOTON_WEBHOOK_SECRET: "whsec_" + Buffer.alloc(bytes).toString("base64") }));
  assert.equal(betaConfig({ ...env, PHOTON_WEBHOOK_SECRET: "whsec_" + Buffer.alloc(24).toString("base64") }).projectId, projectId);
  assert.throws(() => betaConfig({ ...env, PHOTON_WEBHOOK_SECRET: secret.replace(/=$/, "") }));
});

test("webhook bounds simultaneous connections and absolute slow upload duration", async t => {
  const f = fixture(t), server = createBetaWebhook(config, f.store);
  assert.equal(server.maxConnections, 64);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve)); t.after(() => server.close());
  const started = performance.now();
  await new Promise<void>((resolve, reject) => {
    const socket = connect((server.address() as any).port, "127.0.0.1");
    const bound = setTimeout(() => { socket.destroy(); reject(new Error("upload exceeded absolute bound")); }, 12_500);
    const trickle = setInterval(() => socket.write(" "), 500);
    socket.once("connect", () => socket.write("POST /photon/webhook HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 60000\r\n\r\n"));
    socket.on("data", () => {}); socket.on("error", () => {});
    socket.once("close", () => { clearTimeout(bound); clearInterval(trickle); resolve(); });
  });
  assert.ok(performance.now() - started < 12_000);
  assert.deepEqual(f.store.counts(), []);
});
