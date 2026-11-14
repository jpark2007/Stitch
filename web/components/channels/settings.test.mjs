import { test } from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";

registerHooks({ resolve(specifier, context, next) {
  if (specifier === "./settings" && context.parentURL?.endsWith("/channels/client.ts")) return next("./settings.ts", context);
  return next(specifier, context);
} });
const { createSettingsClient, ChannelSettingsError } = await import("./client.ts");
const { preferencesFor, preferenceError, validHandle, parseContact, statusDescription } = await import("./settings.ts");
const prefs = { timezone: "America/New_York", cadenceDays: 7, quietHours: { start: "21:00", end: "08:00" } };
const contact = { personId: "dad", provider: "imessage", handle: "+12125550123", status: "pending", ...prefs };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });

test("preferences accept overnight quiet hours and enforce date/time bounds", () => {
  assert.equal(preferenceError(prefs), null);
  for (const bad of [0, 31, 1.5, NaN]) assert.ok(preferenceError({ ...prefs, cadenceDays: bad }));
  for (const timezone of ["", "bad/zone", " America/New_York"]) assert.ok(preferenceError({ ...prefs, timezone }));
  for (const quietHours of [{ start: "25:00", end: "08:00" }, { start: "21:00", end: "21:00" }]) assert.ok(preferenceError({ ...prefs, quietHours }));
  assert.deepEqual(preferencesFor(null), prefs);
});

test("handle validation requires an international number or email", () => {
  for (const h of ["+12125550123", "david@example.com"]) assert.equal(validHandle(h), true);
  for (const h of ["12125550123", "+012345678", "+123", "name@", "a b@example.com"]) assert.equal(validHandle(h), false);
});

test("contact parser drops private fields and refuses malformed status/preferences", () => {
  assert.deepEqual(parseContact({ ...contact, codeHash: "private", spaceId: "private" }), contact);
  for (const c of [{ ...contact, status: "verified" }, { ...contact, personId: "" }, { ...contact, quietHours: {} }, { ...contact, cadenceDays: 500 }]) assert.throws(() => parseContact(c));
  assert.match(statusDescription("pending"), /No invitation has been sent/);
  assert.match(statusDescription("stopped"), /fresh verification message/);
});

test("client encodes family/person paths, uses cookie credentials and keeps token out of URL", async () => {
  const calls = [];
  const c = createSettingsClient("http://localhost:8012/", async (url, init) => {
    calls.push({ url, init }); return json({ contact: { ...contact, personId: "p/name" }, code: "A".repeat(43), expiresAt: "2030-01-01T00:00:00Z", instruction: "Send LINK to opt in" });
  });
  const signal = new AbortController().signal;
  await c.enroll("family/name", "p/name", contact.handle, prefs, signal);
  assert.equal(calls[0].url, "http://localhost:8012/families/family%2Fname/channels/people/p%2Fname/enrollment");
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(calls[0].init.cache, "no-store");
  assert.equal(calls[0].init.signal, signal);
  assert.deepEqual(JSON.parse(calls[0].init.body), { provider: "imessage", handle: contact.handle, ...prefs });
});

test("error statuses never expose raw backend details or HTML", async () => {
  for (const status of [401, 403, 404, 409, 422, 500, 503]) {
    const c = createSettingsClient("", async () => json({ detail: "private phone +19999999999 token SECRET" }, status));
    await assert.rejects(c.get("f", "dad"), e => e instanceof ChannelSettingsError && e.status === status && !/SECRET|199999/.test(e.message));
  }
});

test("wrong-person or malformed successful responses cannot change displayed consent", async () => {
  for (const payload of [{ contact: { ...contact, personId: "other" } }, { contact: { ...contact, status: "something" } }, {}]) {
    await assert.rejects(createSettingsClient("", async () => json(payload)).get("f", "dad"), ChannelSettingsError);
  }
  await assert.rejects(createSettingsClient("", async () => json({ contact: { ...contact, status: "active" }, code: "A".repeat(43), expiresAt: "2030-01-01", instruction: "x" })).enroll("f", "dad", contact.handle, prefs), ChannelSettingsError);
  await assert.rejects(createSettingsClient("", async () => json({ contact })).stop("f", "dad"), ChannelSettingsError);
});

test("network or malformed JSON failures do not automatically retry mutations", async () => {
  for (const fail of [() => { throw new Error("private network message"); }, () => new Response("<html>secret</html>")]) {
    let count = 0;
    const c = createSettingsClient("", async () => { count++; return fail(); });
    await assert.rejects(c.save("f", "dad", prefs), ChannelSettingsError);
    assert.equal(count, 1);
  }
});

test("caller cancellation stays identifiable and does not retry", async () => {
  const controller = new AbortController(); controller.abort();
  const abort = new DOMException("Aborted", "AbortError");
  const c = createSettingsClient("", async () => { throw abort; });
  await assert.rejects(c.get("f", "dad", controller.signal), e => e === abort);
});
