import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import { imessage } from "spectrum-ts/providers/imessage";
import { asText } from "spectrum-ts/authoring";
import { ApiClient, TerminalOutbox, forwardIncoming, type Receipt } from "../src/bridge.ts";
import { Authenticator, linkCode, parseEnrollment } from "../src/protocol.ts";

const code = "A".repeat(43);
const secret = "0123456789abcdef0123456789abcdef";
type Incoming = Parameters<typeof forwardIncoming>[2];

// Exercise the installed Stable provider schema without starting its lifecycle,
// configuring a cloud client or obtaining any credential. Space metadata comes
// from this provider-shaped object, never the message text/HTTP user payload.
class FakeSdkSpace {
  readonly __platform = "imessage";
  id: string;
  type: "dm" | "group";
  phone: string;
  constructor(type: "dm" | "group" = "dm", id = "opaque-no-id-heuristics") {
    const parsed = imessage.config().__definition.space.schema!.parse({ id, type, phone: "+15550000000" }) as { id: string; type: "dm" | "group"; phone: string };
    this.id = parsed.id; this.type = parsed.type; this.phone = parsed.phone;
  }
  async send() { assert.fail("Enrollment must not send a provider message"); }
}

function message(space = new FakeSdkSpace(), text = `LINK ${code}`, changes: Record<string, unknown> = {}): Incoming {
  return { id: "child-id", platform: "imessage", sender: { id: "+15555550123" },
    direction: "inbound", space, content: asText(text), ...changes } as unknown as Incoming;
}

function recorder() {
  const calls: { path: string; value: unknown }[] = [];
  return { calls, client: { post: async (path: Parameters<ApiClient["post"]>[0], value: Parameters<ApiClient["post"]>[1]): Promise<Receipt> => {
    calls.push({ path, value }); return { status: 200, body: { status: "active" } };
  } } };
}

test("LINK consumes only exact commands and reserves malformed LINK prefixes", () => {
  assert.equal(linkCode(` LINK ${code}\n`), code);
  assert.equal(linkCode(`link ${code}`), code);
  assert.equal(linkCode("My family story"), null);
  for (const text of ["LINK", "LINK short", `LINK ${code} extra`, `LINK\n${code}`, `LINK${code}`, `\u0085LINK ${code} private`, `Linked ${code}`]) {
    assert.throws(() => linkCode(text), (error: Error) => error.message === "Invalid LINK command; no story was recorded" && !error.message.includes(code));
  }
});

test("schema-valid SDK DM routes LINK exclusively to enrollment, retaining child and sender IDs", async () => {
  const space = new FakeSdkSpace(), { client, calls } = recorder();
  assert.equal((await forwardIncoming(client, space, message(space)))[0].status, 200);
  assert.deepEqual(calls, [{ path: "/internal/channels/enroll", value: {
    messageId: "child-id", provider: "imessage", senderId: "+15555550123",
    spaceId: space.id, spaceKind: "direct", code,
  } }]);
});

test("group, unknown, forged content metadata, other provider and outbound LINK never reach API", async () => {
  const { client, calls } = recorder();
  for (const space of [new FakeSdkSpace("group", "iMessage;-;+15555550123"),
    { id: "iMessage;-;+15555550123", __platform: "imessage" },
    { id: "space", __platform: "terminal", type: "dm" },
    { id: "space", __platform: "imessage", type: "direct" }]) {
    const incoming = message(new FakeSdkSpace(), `LINK ${code}`, { space, spaceKind: "direct" });
    await assert.rejects(forwardIncoming(client, space, incoming), /verified direct/);
  }
  const space = new FakeSdkSpace();
  for (const changes of [{ direction: "outbound" }, { platform: "terminal" }, { sender: undefined }, { space: new FakeSdkSpace("dm", "other-space") }]) {
    await assert.rejects(forwardIncoming(client, space, message(space, `LINK ${code}`, changes)), /verified direct/);
  }
  await assert.rejects(forwardIncoming(client, space, message(space, `LINK ${code} extra`)), /Invalid LINK/);
  assert.deepEqual(calls, []);
});

test("normal text remains text; nested grouped LINK children cannot leak token into story intake", async () => {
  const space = new FakeSdkSpace(), { client, calls } = recorder();
  const group = message(space, "unused", { content: { type: "group", items: [
    message(space, "A family memory", { id: "text-child" }), message(space, `LINK ${code}`, { id: "link-child" }),
  ] } });
  await forwardIncoming(client, space, group);
  assert.deepEqual(calls.map((call) => call.path), ["/internal/channels/inbound", "/internal/channels/enroll"]);
  assert(!JSON.stringify(calls[0]).includes(code));
  assert.equal((calls[1].value as { messageId: string }).messageId, "link-child");
});

test("enrollment payload is strict and private-space assertion is required", () => {
  const payload = { messageId: "m", provider: "imessage", senderId: "+15555550123", spaceId: "s", spaceKind: "direct", code };
  assert.deepEqual(parseEnrollment(payload), payload);
  for (const change of [{ spaceKind: undefined }, { spaceKind: "group" }, { provider: "terminal" }, { familyId: "spoof" }, { code: "bad" }, { senderId: " " }, { spaceId: "é".repeat(129) }]) {
    assert.throws(() => parseEnrollment({ ...payload, ...change }), /Invalid/);
  }
});

test("actual loopback enrollment signs direct proof, never sends code to inbound or retries uncertain failure", async () => {
  const auth = new Authenticator(secret);
  const received: { path: string; body: Record<string, unknown> }[] = [];
  const server = createServer(async (req, res) => {
    try {
      const chunks: Buffer[] = [];
      for await (const chunk of req) chunks.push(Buffer.from(chunk));
      const raw = Buffer.concat(chunks), headers = new Headers();
      for (const [key, value] of Object.entries(req.headers)) if (value) headers.set(key, Array.isArray(value) ? value.join(",") : value);
      auth.verify("POST", req.url!, headers, raw);
      received.push({ path: req.url!, body: JSON.parse(raw.toString()) });
      res.setHeader("content-type", "application/json");
      res.statusCode = received.length === 1 ? 200 : 503;
      res.end(JSON.stringify({ status: "active" }));
    } catch { res.statusCode = 400; res.end("{}"); }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address(); assert(address && typeof address !== "string");
    const client = new ApiClient(`http://127.0.0.1:${address.port}`, secret, { attempts: 3, delay: async () => {} });
    const space = new FakeSdkSpace();
    await forwardIncoming(client, space, message(space));
    await assert.rejects(forwardIncoming(client, space, message(space)), /API rejected request/);
    assert.equal(received.length, 2);
    assert(received.every((r) => r.path === "/internal/channels/enroll" && r.body.spaceKind === "direct"));
  } finally { server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())); }
});

test("fresh outbox refuses persisted-looking destinations rather than guessing or creating a chat", async () => {
  const outbox = new TerminalOutbox(500, "imessage");
  await assert.rejects(outbox.send({ messageId: "prompt", provider: "imessage", spaceId: "iMessage;-;+15555550123", text: "prompt" }), /Unknown provider space/);
});
