import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { ApiClient, type Receipt } from "../src/bridge.ts";
import { Authenticator, MAX_VOICE_BYTES, MAX_VOICE_BODY_BYTES, VOICE_TYPES, parseVoice } from "../src/protocol.ts";
import { downloadVoice, forwardVoice } from "../src/voice.ts";
import { forwardProviderMessage, type ProviderMessage } from "../src/photos.ts";

const identity = { messageId: "1:parent", provider: "imessage" as const, spaceId: "room", senderId: "person" };
const payload = { ...identity, attachmentId: "voice-id", mimeType: "audio/mp4", dataBase64: "AQID" };
const source = (bytes = Uint8Array.of(1, 2, 3)) => ({ id: "voice-id", mimeType: "audio/mp4", stream: async () => new ReadableStream<Uint8Array>({ start(c) { c.enqueue(bytes); c.close(); } }) });
const accepted: Receipt = { status: 202, body: { accepted: true, duplicate: false } };

test("native iMessage CAF remains CAF in the signed envelope for backend conversion", async () => {
  const bytes = Buffer.from([0x63, 0x61, 0x66, 0x66, 0, 1, 0, 0]);
  const requests: { path: string; value: unknown }[] = [];
  const client = { post: async (path: Parameters<ApiClient["post"]>[0], value: Parameters<ApiClient["post"]>[1]) => {
    requests.push({ path, value });
    return path.endsWith("authorize") ? { status: 200, body: { allowed: true } } : accepted;
  } };
  await forwardVoice(client, identity, { ...source(bytes), mimeType: "audio/x-caf" });
  assert.equal(requests[0].path, "/internal/channels/authorize");
  assert.deepEqual(requests[1], { path: "/internal/channels/inbound-voice", value: {
    ...identity, attachmentId: "voice-id", mimeType: "audio/x-caf", dataBase64: bytes.toString("base64"),
  } });
  assert.equal(parseVoice(requests[1].value).mimeType, "audio/x-caf");
});

test("strict voice schema allows only supported audio with bounded IDs and canonical base64", () => {
  for (const mimeType of VOICE_TYPES) assert.equal(parseVoice({ ...payload, mimeType }).mimeType, mimeType);
  for (const change of [{ caption: "not permitted" }, { url: "https://evil.invalid" }, { familyId: "spoof" }, { mimeType: "audio/caf" }, { mimeType: "image/png" }, { attachmentId: " " }, { attachmentId: "é".repeat(129) }, { messageId: "\ud800" }, { dataBase64: "AB==" }, { dataBase64: "" }, { dataBase64: "AQID\n" }, { dataBase64: "AQI" }]) assert.throws(() => parseVoice({ ...payload, ...change }), /Invalid/);
  assert.throws(() => parseVoice({ ...payload, dataBase64: Buffer.alloc(MAX_VOICE_BYTES + 1).toString("base64") }), /encoding/);
});

test("authorization fails before opening voice bytes", async () => {
  let opened = 0;
  const voice = { ...source(), stream: async () => { opened++; return source().stream(); } };
  for (const body of [null, {}, { allowed: false }, { allowed: "true" }]) await assert.rejects(forwardVoice({ post: async () => ({ status: 200, body }) }, identity, voice), /not authorized/);
  await assert.rejects(forwardVoice({ post: async () => { throw new Error("denied"); } }, identity, voice), /denied/);
  await assert.rejects(forwardVoice({ post: async () => { throw new Error("must not reach"); } }, identity, { ...voice, mimeType: "audio/caf" }), /metadata/);
  assert.equal(opened, 0);
});

test("voice IDs survive redelivery and absent SDK ID falls back to child message ID", async () => {
  const seen: { path: string; value: unknown }[] = [];
  const client = { post: async (path: Parameters<ApiClient["post"]>[0], value: Parameters<ApiClient["post"]>[1]) => {
    seen.push({ path, value }); return path.endsWith("authorize") ? { status: 200, body: { allowed: true } } : accepted;
  } };
  const voice = { ...source(), read: async () => { throw new Error("unbounded read forbidden"); }, url: "http://169.254.169.254/private" };
  await forwardVoice(client, identity, voice);
  await forwardVoice(client, identity, voice);
  await forwardVoice(client, identity, { ...voice, id: undefined });
  assert.deepEqual(seen[0], { path: "/internal/channels/authorize", value: { provider: "imessage", senderId: "person" } });
  assert.deepEqual(seen[1], { path: "/internal/channels/inbound-voice", value: payload });
  assert.deepEqual(seen[1], seen[3]);
  assert.deepEqual(seen[5].value, { ...payload, attachmentId: identity.messageId });
});

test("SDK grouped text and voice preserve child identities and order", async () => {
  const seen: Record<string, unknown>[] = [];
  const client = { post: async (path: Parameters<ApiClient["post"]>[0], value: Parameters<ApiClient["post"]>[1]) => {
    if (path.endsWith("authorize")) return { status: 200, body: { allowed: true } };
    seen.push(value); return accepted;
  } };
  const child = (id: string, content: unknown) => ({ id, platform: "imessage", sender: { id: "person" }, content });
  const group = child("parent", { type: "group", items: [child("0:parent", { type: "text", text: "My story" }), child("1:parent", { ...source(), type: "voice" }), child("2:parent", { ...source(), type: "voice", id: undefined })] }) as unknown as ProviderMessage;
  assert.equal((await forwardProviderMessage(client, "room", group)).length, 3);
  assert.deepEqual(seen.map((v) => v.messageId), ["0:parent", "1:parent", "2:parent"]);
  assert.deepEqual(seen.slice(1).map((v) => v.attachmentId), ["voice-id", "2:parent"]);
});

test("oversized voice cancels and declared invalid size never opens stream", async () => {
  let canceled = false;
  await assert.rejects(downloadVoice({ ...source(), size: 1, stream: async () => new ReadableStream({ start(c) { c.enqueue(new Uint8Array(MAX_VOICE_BYTES + 1)); }, cancel() { canceled = true; } }) }), /exceeds/);
  assert(canceled);
  await assert.rejects(downloadVoice({ ...source(), size: MAX_VOICE_BYTES + 1, stream: async () => { assert.fail("must not open"); } }), /size/);
  assert.equal((await downloadVoice(source(new Uint8Array(MAX_VOICE_BYTES)))).length, MAX_VOICE_BYTES);
});

test("stalled and late-opening voice streams cancel without hanging on cancellation", async () => {
  let canceled = 0;
  await assert.rejects(downloadVoice({ ...source(), stream: async () => new ReadableStream({ cancel() { canceled++; return new Promise(() => {}); } }) }, { timeoutMs: 10 }), /timed out/);
  let open!: (s: ReadableStream<Uint8Array>) => void;
  const pending = new Promise<ReadableStream<Uint8Array>>((resolve) => { open = resolve; });
  await assert.rejects(downloadVoice({ ...source(), stream: () => pending }, { timeoutMs: 10 }), /timed out/);
  open(new ReadableStream({ cancel() { canceled++; } }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(canceled, 2);
});

test("absolute deadline rejects endless immediately available empty chunks", async () => {
  let canceled = false;
  await assert.rejects(downloadVoice({ ...source(), stream: async () => new ReadableStream({ pull(c) { c.enqueue(new Uint8Array()); }, cancel() { canceled = true; } }) }, { timeoutMs: 10 }), /timed out/);
  assert(canceled);
});

test("fragmented streams retain exact bytes even when SDK reuses its buffer", async () => {
  let index = 0;
  const scratch = new Uint8Array(1000);
  const expected = Buffer.concat(Array.from({ length: 100 }, (_, n) => Buffer.alloc(1000, n)));
  const bytes = await downloadVoice({ ...source(), stream: async () => new ReadableStream({
    pull(c) {
      if (index === 100) { c.close(); return; }
      scratch.fill(index++);
      c.enqueue(scratch);
    },
  }, { highWaterMark: 0 }) });
  assert.deepEqual(bytes, expected);
});

test("many tiny and empty chunks do not retain a deadline reaction per read", () => {
  const moduleUrl = new URL("../src/voice.ts", import.meta.url).href;
  // Measure live retention before download completion in a separate GC-enabled
  // process. The old per-read Promise.race retained >20 MiB for these 100k reads.
  const script = `
    import { downloadVoice } from ${JSON.stringify(moduleUrl)};
    global.gc();
    const before = process.memoryUsage().heapUsed;
    let n = 0, retained = 0;
    const bytes = await downloadVoice({ mimeType: "audio/wav", stream: async () => new ReadableStream({
      pull(c) {
        if (n === 100000) { global.gc(); retained = process.memoryUsage().heapUsed - before; c.close(); return; }
        c.enqueue(n++ % 2 ? Uint8Array.of(7) : new Uint8Array());
      }
    }, { highWaterMark: 0 }) });
    process.stdout.write(JSON.stringify({ retained, length: bytes.length }));
  `;
  const result = spawnSync(process.execPath, ["--expose-gc", "--input-type=module", "-e", script], { encoding: "utf8", timeout: 10_000 });
  assert.equal(result.status, 0, result.stderr);
  const measured = JSON.parse(result.stdout);
  assert.equal(measured.length, 50_000);
  assert(measured.retained < 8 * 1024 * 1024, `Retained ${measured.retained} bytes for 100k fragmented reads`);
});

test("empty, invalid, unsupported and failed streams reject without provider details", async () => {
  await assert.rejects(downloadVoice({ ...source(), stream: async () => new ReadableStream({ start(c) { c.close(); } }) }), /empty/);
  await assert.rejects(downloadVoice({ ...source(), mimeType: "audio/caf" }), /Unsupported/);
  await assert.rejects(downloadVoice({ ...source(), stream: async () => { throw new Error("private credential"); } }), { message: "Voice stream failed" });
  for (const timeoutMs of [0, -1, Infinity, NaN, 60_001]) await assert.rejects(downloadVoice(source(), { timeoutMs }), /timeout/);
});

test("real loopback HTTP voice retries retain payload and renew signed nonce", async () => {
  const secret = "0123456789abcdef0123456789abcdef";
  const auth = new Authenticator(secret);
  const voices: { raw: Buffer; nonce: string }[] = [];
  const server = createServer(async (req, res) => {
    try {
      const chunks: Buffer[] = [];
      for await (const chunk of req) chunks.push(Buffer.from(chunk));
      const raw = Buffer.concat(chunks);
      const headers = new Headers();
      for (const [key, value] of Object.entries(req.headers)) if (value) headers.set(key, Array.isArray(value) ? value.join(",") : value);
      auth.verify("POST", req.url!, headers, raw);
      res.setHeader("content-type", "application/json");
      if (req.url === "/internal/channels/authorize") { res.end('{"allowed":true}'); return; }
      assert.equal(req.url, "/internal/channels/inbound-voice");
      voices.push({ raw, nonce: headers.get("x-channels-nonce")! });
      res.statusCode = voices.length === 1 ? 503 : 202;
      res.end(JSON.stringify({ accepted: voices.length > 1, duplicate: true }));
    } catch { res.statusCode = 400; res.end("{}"); }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address(); assert(address && typeof address !== "string");
    const client = new ApiClient(`http://127.0.0.1:${address.port}`, secret, { delay: async () => {} });
    const receipt = await forwardVoice(client, identity, source());
    assert.equal(receipt.status, 202);
    assert.equal(voices.length, 2);
    assert.deepEqual(voices[0].raw, voices[1].raw);
    assert.notEqual(voices[0].nonce, voices[1].nonce);
    assert.deepEqual(JSON.parse(voices[0].raw.toString()), payload);
  } finally { server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())); }
});

test("maximum decoded voice passes only its exact large-body path; text limits unchanged", async () => {
  let sent = 0;
  const client = new ApiClient("http://localhost:8000", "0123456789abcdef0123456789abcdef", { fetch: (async (_url, init) => {
    sent++; assert(Buffer.byteLength(init!.body as Uint8Array) <= MAX_VOICE_BODY_BYTES); return Response.json({ accepted: true });
  }) as typeof fetch });
  const dataBase64 = Buffer.alloc(MAX_VOICE_BYTES).toString("base64");
  await client.post("/internal/channels/inbound-voice", { ...payload, dataBase64 });
  await assert.rejects(client.post("/internal/channels/inbound", { ...identity, text: "\\".repeat(8000), spaceId: "\n".repeat(255) + "s" }), /too large/);
  assert.equal(sent, 1);
});
