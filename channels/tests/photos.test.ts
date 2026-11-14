import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiClient, TerminalOutbox, type Receipt } from "../src/bridge.ts";
import { Authenticator, MAX_PHOTO_BODY_BYTES, MAX_PHOTO_BYTES, configureProviderEnvironment, parseAuthorization, parsePhoto, type InboundPhoto } from "../src/protocol.ts";
import { downloadPhoto, forwardPhoto, forwardProviderMessage, type ProviderMessage } from "../src/photos.ts";

const secret = "0123456789abcdef0123456789abcdef";
const identity = { messageId: "message-photo-1", provider: "terminal" as const, spaceId: "chat-1", senderId: "person-1" };
const photo = { ...identity, attachmentId: "attachment-1", mimeType: "image/png", dataBase64: "AQID", caption: "" };
const source = (bytes = Uint8Array.of(1, 2, 3)) => ({ id: "attachment-1", mimeType: "image/png", stream: async () => new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(bytes); controller.close(); } }) });
const receipt: Receipt = { status: 202, body: { accepted: true, duplicate: false } };

test("photo and authorization shapes are strict; caption can be empty, IDs cannot", () => {
  assert.deepEqual(parsePhoto(photo), photo);
  assert.deepEqual(parseAuthorization({ provider: "terminal", senderId: "p" }), { provider: "terminal", senderId: "p" });
  assert.throws(() => parseAuthorization({ provider: "terminal", senderId: "p", familyId: "spoof" }));
  for (const change of [{ caption: null }, { caption: "é".repeat(4001) }, { caption: "\uD800" }, { attachmentId: " " }, { attachmentId: "é".repeat(129) }, { familyId: "spoof" }, { mimeType: "application/pdf" }, { dataBase64: "AB==" }, { dataBase64: "AQI" }, { dataBase64: "AQID\n" }, { dataBase64: "" }, { dataBase64: "AQ=I" }]) {
    assert.throws(() => parsePhoto({ ...photo, ...change }), /Invalid/);
  }
  assert.equal(parsePhoto({ ...photo, caption: "é".repeat(4000) }).caption?.length, 4000);
});

test("photo authorization denial occurs before reading any attachment bytes", async () => {
  let opened = 0;
  const attachment = { ...source(), stream: async () => { opened++; return source().stream(); } };
  for (const body of [{ allowed: false }, { allowed: "true" }, {}, null]) {
    await assert.rejects(forwardPhoto({ post: async () => ({ status: 200, body }) }, identity, attachment), /not authorized/);
  }
  await assert.rejects(forwardPhoto({ post: async () => { throw new Error("authorization rejected"); } }, identity, attachment), /authorization rejected/);
  assert.equal(opened, 0);
});

test("photo forwarding preserves provider/message/attachment identity and uses stream, not read or URLs", async () => {
  const requests: { path: string; value: unknown }[] = [];
  const client = { post: async (path: Parameters<ApiClient["post"]>[0], value: Parameters<ApiClient["post"]>[1]): Promise<Receipt> => {
    requests.push({ path, value }); return path.endsWith("authorize") ? { status: 200, body: { allowed: true } } : receipt;
  } };
  const attachment = { ...source(), read: async () => { throw new Error("Unbounded read must not be called"); }, url: "http://169.254.169.254/private" };
  assert.deepEqual(await forwardPhoto(client, identity, attachment, "An old photo"), receipt);
  assert.deepEqual(requests, [
    { path: "/internal/channels/authorize", value: { provider: "terminal", senderId: "person-1" } },
    { path: "/internal/channels/inbound-photo", value: { ...photo, caption: "An old photo" } },
  ]);
});

test("photo stream is bounded even when SDK size metadata lies; oversized streams cancel", async () => {
  let canceled = false;
  await assert.rejects(downloadPhoto({ ...source(), size: 1, stream: async () => new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(MAX_PHOTO_BYTES + 1)); },
    cancel() { canceled = true; },
  }) }), /exceeds 20 MiB/);
  assert.equal(canceled, true);
  let opened = 0;
  await assert.rejects(downloadPhoto({ ...source(), size: MAX_PHOTO_BYTES + 1, stream: async () => { opened++; return source().stream(); } }), /size/);
  assert.equal(opened, 0);
});

test("stalled stream times out and cancels without awaiting a hanging cancel", async () => {
  let canceled = false;
  const start = Date.now();
  await assert.rejects(downloadPhoto({ ...source(), stream: async () => new ReadableStream({
    cancel() { canceled = true; return new Promise(() => {}); },
  }) }, { timeoutMs: 10 }), /timed out/);
  assert.equal(canceled, true);
  assert(Date.now() - start < 1000);
});

test("late SDK stream opening is timed out and canceled when it eventually resolves", async () => {
  let canceled = false;
  let open!: (value: ReadableStream<Uint8Array>) => void;
  const opening = new Promise<ReadableStream<Uint8Array>>((resolve) => { open = resolve; });
  await assert.rejects(downloadPhoto({ ...source(), stream: () => opening }, { timeoutMs: 10 }), /timed out/);
  open(new ReadableStream({ cancel() { canceled = true; } }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(canceled, true);
});

test("unsupported, empty and failing streams fail without exposing provider errors", async () => {
  await assert.rejects(downloadPhoto({ ...source(), mimeType: "audio/mp4" }), /Unsupported/);
  await assert.rejects(downloadPhoto({ ...source(), stream: async () => new ReadableStream({ start(controller) { controller.close(); } }) }), /empty/);
  await assert.rejects(downloadPhoto({ ...source(), stream: async () => { throw new Error("secret provider token"); } }), { message: "Photo stream failed" });
});

test("signed photo retry keeps content and IDs but renews nonce; authorization uses small body", async () => {
  const attempts: { path: string; raw: Buffer; headers: Headers }[] = [];
  const client = new ApiClient("http://localhost:8000", secret, {
    fetch: (async (url, init) => {
      const path = new URL(String(url)).pathname;
      const raw = Buffer.from(init!.body as Uint8Array), headers = new Headers(init!.headers);
      new Authenticator(secret).verify("POST", path, headers, raw);
      attempts.push({ path, raw, headers });
      if (path.endsWith("authorize")) return Response.json({ allowed: true });
      return attempts.filter((a) => a.path.endsWith("inbound-photo")).length === 1 ? new Response("{}", { status: 503 }) : Response.json({ accepted: true, duplicate: true }, { status: 202 });
    }) as typeof fetch,
    delay: async () => {},
  });
  await forwardPhoto(client, identity, source());
  assert.equal(attempts.length, 3);
  assert.deepEqual(attempts[1].raw, attempts[2].raw);
  assert.notEqual(attempts[1].headers.get("x-channels-nonce"), attempts[2].headers.get("x-channels-nonce"));
  assert.equal(JSON.parse(attempts[1].raw.toString()).messageId, identity.messageId);
});

test("exact photo JSON body cap accepted, one byte over refused; text limit stays small", async () => {
  let sent = 0;
  const client = new ApiClient("http://localhost:8000", secret, { fetch: (async () => { sent++; return Response.json({ accepted: true }); }) as typeof fetch });
  const large: InboundPhoto = { ...photo, dataBase64: Buffer.alloc(MAX_PHOTO_BYTES).toString("base64"), caption: "\\".repeat(8000), spaceId: "s" };
  const gap = MAX_PHOTO_BODY_BYTES - Buffer.byteLength(JSON.stringify(large));
  assert(gap >= 0 && gap < 255);
  large.spaceId = "s".repeat(gap + 1);
  assert.equal(Buffer.byteLength(JSON.stringify(large)), MAX_PHOTO_BODY_BYTES);
  await client.post("/internal/channels/inbound-photo", large);
  await assert.rejects(client.post("/internal/channels/inbound-photo", { ...large, spaceId: `${large.spaceId}s` }), /too large/);
  await assert.rejects(client.post("/internal/channels/inbound", { ...identity, spaceId: "\n".repeat(255) + "s", text: "\\".repeat(8000) }), /too large/);
  assert.equal(sent, 1);
});

test("terminal remains default and drops inherited cloud credentials; Stable cloud is disabled", () => {
  const env: NodeJS.ProcessEnv = { SPECTRUM_PROJECT_ID: "old", SPECTRUM_PROJECT_SECRET: "private", SPECTRUM_WEBHOOK_SECRET: "private" };
  assert.equal(configureProviderEnvironment(env), "terminal");
  assert.deepEqual(env, {});
  assert.throws(() => configureProviderEnvironment({ CHANNELS_PROVIDER: "imessage", SPECTRUM_PROJECT_ID: "stable-project", SPECTRUM_PROJECT_SECRET: "private" }));
  for (const bad of [{ CHANNELS_PROVIDER: "sms" }, { CHANNELS_PROVIDER: "imessage" }, { CHANNELS_PROVIDER: "imessage", SPECTRUM_PROJECT_ID: "pho_prj_beta", SPECTRUM_PROJECT_SECRET: "private" }]) assert.throws(() => configureProviderEnvironment(bad));
});

test("outbox routes only registered spaces on explicitly enabled provider", async () => {
  const outbox = new TerminalOutbox(50, "imessage");
  const sent: string[] = [];
  outbox.register("same-space", async (text) => { sent.push(text); }, "imessage");
  const message = { messageId: "m", provider: "imessage" as const, spaceId: "same-space", text: "reply" };
  await outbox.send(message); await outbox.send(message);
  assert.deepEqual(sent, ["reply"]);
  await assert.rejects(outbox.send({ ...message, provider: "terminal" }), /configured/);
  assert.throws(() => outbox.register("same-space", async () => {}, "terminal"), /not enabled/);
  await assert.rejects(outbox.send({ ...message, messageId: "new", spaceId: "unknown" }), /Unknown/);
});

test("grouped SDK text plus multiple attachments preserves child order and unique IDs", async () => {
  const seen: { path: string; value: unknown }[] = [];
  const client = { post: async (path: Parameters<ApiClient["post"]>[0], value: Parameters<ApiClient["post"]>[1]): Promise<Receipt> => {
    seen.push({ path, value }); return path.endsWith("authorize") ? { status: 200, body: { allowed: true } } : receipt;
  } };
  const child = (id: string, content: unknown) => ({ id, platform: "imessage", sender: { id: "phone" }, content });
  const group = child("parent", { type: "group", items: [child("0:parent", { type: "text", text: "My birthday" }), child("1:parent", { ...source(), type: "attachment" }), child("2:parent", { ...source(), id: "attachment-2", type: "attachment" })] }) as unknown as ProviderMessage;
  assert.equal((await forwardProviderMessage(client, "room", group)).length, 3);
  const messages = seen.filter((x) => !x.path.endsWith("authorize")).map((x) => x.value as Record<string, unknown>);
  assert.deepEqual(messages.map((x) => x.messageId), ["0:parent", "1:parent", "2:parent"]);
  assert.deepEqual(messages.slice(1).map((x) => x.attachmentId), ["attachment-1", "attachment-2"]);
  assert(messages.every((x) => x.provider === "imessage" && x.senderId === "phone" && x.spaceId === "room"));
});
