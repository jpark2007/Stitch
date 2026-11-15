import { test } from "node:test";
import assert from "node:assert/strict";
import { createCallsClient, CallSettingsError } from "./calls-client.ts";

const json = (value, status = 200) => new Response(JSON.stringify(value), { status });
const pairing = { code: "1234-5678-9012", personId: "p/name", expiresAt: "2030-01-01T00:00:00Z", instruction: "Say STITCH", phoneNumber: "+12292875827" };
const draft = { id: "draft/one", personId: "p/name", createdAt: "2026-11-15T00:00:00Z", text: "小时候，奶奶教我包饺子。" };

test("pairing uses cookie-authenticated no-store POST and keeps code out of URL", async () => {
  const calls = [];
  const c = createCallsClient("http://localhost:8012/", async (url, init) => { calls.push({ url, init }); return json(pairing, 201); });
  const signal = new AbortController().signal;
  assert.deepEqual(await c.pair("family/name", "p/name", signal), pairing);
  assert.equal(calls[0].url, "http://localhost:8012/families/family%2Fname/channels/people/p%2Fname/call-pairing");
  assert.equal(calls[0].init.body, "{}");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(calls[0].init.cache, "no-store");
  assert.equal(calls[0].init.signal, signal);
});

test("draft list and detail preserve Chinese and reject another person's transcript", async () => {
  const c = createCallsClient("", async url => json(url.endsWith("call-drafts") ? { drafts: [draft] } : { draft }));
  assert.deepEqual(await c.list("f", "p/name"), [draft]);
  assert.deepEqual(await c.get("f", "p/name", draft.id), draft);
  await assert.rejects(c.list("f", "another-person"), CallSettingsError);
  await assert.rejects(c.get("f", "p/name", "wrong-id"), CallSettingsError);
});

test("delete uses encoded ID and accepts an empty 204 response", async () => {
  const c = createCallsClient("", async (url, init) => {
    assert.equal(url, "/families/f/channels/call-drafts/draft%2Fone");
    assert.equal(init.method, "DELETE"); assert.equal(init.credentials, "include"); assert.equal(init.cache, "no-store");
    assert.equal(init.body, undefined); return new Response(null, { status: 204 });
  });
  await c.remove("f", draft.id);
});

test("malformed pairing cannot inject a phone link or wrong identity", async () => {
  for (const bad of [{ phoneNumber: "javascript:alert(1)" }, { code: "1234" }, { expiresAt: "invalid" }, { personId: "other" }]) {
    const c = createCallsClient("", async () => json({ ...pairing, ...bad }));
    await assert.rejects(c.pair("f", "p/name"), CallSettingsError);
  }
});

test("errors are bounded UI messages without leaking response content", async () => {
  for (const status of [401, 403, 404, 429, 500]) {
    const c = createCallsClient("", async () => json({ detail: "SECRET_TRANSCRIPT" }, status));
    await assert.rejects(c.list("f", "p/name"), e => e.status === status && !e.message.includes("SECRET"));
  }
  const c = createCallsClient("", async () => new Response("not json"));
  await assert.rejects(c.list("f", "p/name"), CallSettingsError);
});

test("request abort remains identifiable and is forwarded to fetch", async () => {
  const controller = new AbortController(); controller.abort();
  const reason = new DOMException("Aborted", "AbortError");
  const c = createCallsClient("", async (_, init) => { assert.equal(init.signal, controller.signal); throw reason; });
  await assert.rejects(c.list("f", "p/name", controller.signal), e => e === reason);
});
