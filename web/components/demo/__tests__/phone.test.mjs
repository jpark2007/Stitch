// Run with: cd web && node --test --experimental-strip-types "components/demo/__tests__/*.test.mjs"
// Never a real number: +1 555 555 01xx only.
import { describe, test } from "node:test";
import assert from "node:assert/strict";

import {
  PROMPT_NOW_UNAVAILABLE,
  TEXTING_OFF,
  channelErrorCopy,
  demoPreferences,
  expiryCopy,
  normalizePhone,
  probePromptNow,
  promptNow,
  readPromptNow,
  statusCopy,
} from "../phone.ts";

describe("normalizePhone", () => {
  test("E.164 numbers, with formatting dropped", () => {
    assert.equal(normalizePhone("+15555550100"), "+15555550100");
    assert.equal(normalizePhone(" +1 (555) 555-0100 "), "+15555550100");
    assert.equal(normalizePhone("+1.555.555.0101"), "+15555550101");
  });

  test("no country code, a leading zero, too short or long, letters, email: null", () => {
    for (const bad of ["5555550100", "+05555550100", "+1555", "+1234567890123456", "+1555555abcd", "a@b.co", "", "+"]) {
      assert.equal(normalizePhone(bad), null, bad);
    }
  });
});

describe("demoPreferences", () => {
  test("the browser's zone, weekly, the settings page's quiet hours", () => {
    assert.deepEqual(demoPreferences("America/Chicago"), {
      timezone: "America/Chicago",
      cadenceDays: 7,
      quietHours: { start: "21:00", end: "08:00" },
    });
    assert.equal(demoPreferences("").timezone, "America/New_York");
    assert.equal(demoPreferences(undefined).timezone, "America/New_York");
  });
});

describe("copy", () => {
  test("every status has a label and a line", () => {
    for (const s of ["none", "pending", "active", "stopped"]) {
      const c = statusCopy(s);
      assert.ok(c.label && c.line, s);
    }
    assert.equal(statusCopy("active").label, "Paired");
  });

  test("Channels errors: the rule behind a 403 is named; unknown statuses keep the client's words", () => {
    assert.match(channelErrorCopy(403, "x", "Sam", true), /Only Sam can pair their own phone/);
    assert.match(channelErrorCopy(403, "x", "Rosa", false), /Rosa has no account, so only a family admin/);
    assert.match(channelErrorCopy(409, "x", "Rosa", false), /already paired/);
    assert.match(channelErrorCopy(429, "x", "Rosa", false), /Too many/);
    assert.equal(channelErrorCopy(500, "The change could not be confirmed.", "Rosa", false), "The change could not be confirmed.");
    assert.ok(TEXTING_OFF.includes("off"));
  });

  test("expiry: clock time and what is left; past is expired", () => {
    const now = Date.parse("2026-11-15T15:00:00Z");
    const e = expiryCopy("2026-11-15T15:09:30Z", now, "UTC");
    assert.equal(e.expired, false);
    assert.equal(e.text, "Expires at 3:09 PM (9 min 30 s left)");
    assert.equal(expiryCopy("2026-11-15T15:00:20Z", now, "UTC").text, "Expires at 3:00 PM (20 s left)");
    assert.equal(expiryCopy("2026-11-15T14:59:00Z", now, "UTC").expired, true);
    assert.deepEqual(expiryCopy("not a date", now), { expired: false, text: "" });
  });
});

function json(status, body) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("prompt-now", () => {
  test("sent and blocked (with its reason)", async () => {
    assert.deepEqual(await readPromptNow(json(200, { status: "sent" })), { kind: "sent" });
    assert.deepEqual(await readPromptNow(json(200, { status: "blocked", reason: "Quiet hours" })), {
      kind: "blocked",
      reason: "Quiet hours",
    });
    assert.deepEqual(await readPromptNow(json(200, { status: "blocked" })), { kind: "blocked", reason: "No reason given." });
  });

  test("404 and 405: not on this server", async () => {
    assert.deepEqual(await readPromptNow(json(404, { detail: "Not Found" })), { kind: "unavailable" });
    assert.deepEqual(await readPromptNow(json(405, { detail: "Method Not Allowed" })), { kind: "unavailable" });
    assert.ok(PROMPT_NOW_UNAVAILABLE.includes("weekly schedule"));
  });

  test("other errors carry the server's detail when it has one", async () => {
    const r = await readPromptNow(json(409, { detail: "This contact is not active" }));
    assert.deepEqual(r, { kind: "error", message: "This contact is not active", status: 409 });
    assert.equal((await readPromptNow(json(500))).kind, "error");
    assert.equal((await readPromptNow(json(200, { what: 1 }))).kind, "error");
  });

  test("the POST: url, method, body, cookie", async () => {
    const calls = [];
    const fetcher = async (url, init) => {
      calls.push({ url, init });
      return json(200, { status: "sent" });
    };
    assert.deepEqual(await promptNow("http://api.test/", "fam 1", "p/2", fetcher), { kind: "sent" });
    assert.equal(calls[0].url, "http://api.test/families/fam%201/channels/people/p%2F2/prompt-now");
    assert.equal(calls[0].init.method, "POST");
    assert.equal(calls[0].init.body, "{}");
    assert.equal(calls[0].init.credentials, "include");
  });

  test("no answer is an error, not a throw", async () => {
    const r = await promptNow("http://api.test", "f", "p", async () => {
      throw new TypeError("Failed to fetch");
    });
    assert.equal(r.kind, "error");
    assert.equal(r.status, 0);
  });

  test("the probe reads 405 as there and 404 as not, without a POST", async () => {
    const methods = [];
    const answer = (status) => async (_url, init) => {
      methods.push(init.method);
      return json(status, { detail: "x" });
    };
    assert.equal(await probePromptNow("http://api.test", "f", "p", answer(405)), "available");
    assert.equal(await probePromptNow("http://api.test", "f", "p", answer(404)), "unavailable");
    assert.equal(await probePromptNow("http://api.test", "f", "p", answer(401)), "unknown");
    assert.equal(
      await probePromptNow("http://api.test", "f", "p", async () => {
        throw new TypeError("offline");
      }),
      "unknown",
    );
    assert.deepEqual([...new Set(methods)], ["GET"]);
  });
});
