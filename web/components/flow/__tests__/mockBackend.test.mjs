// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { buildDemoSeed } from "../mock/demoSeed.ts";
import {
  createMockBackend,
  FOLLOW_UP_QUESTION,
  MAX_MEDIA_BYTES,
  MOCK_STORAGE_KEY,
} from "../mock/mockBackend.ts";
import { createApiClient, isApiError } from "../../../lib/api.ts";

const SAMPLE = JSON.parse(
  fs.readFileSync(new URL("../../../public/samples/hymn_books.memory.json", import.meta.url), "utf8"),
);
const NOW = new Date("2026-11-13T12:00:00Z");
const BASE = "http://localhost:8000";
const LONG_TEXT =
  "It was the summer we drove to the lake in the old car. Everyone was squeezed in the back seat, " +
  "the radio was playing, and we stopped for ice cream twice before lunch. It rained the whole afternoon.";

function makeBackend(extra = {}) {
  return createMockBackend({ seed: () => buildDemoSeed(SAMPLE, NOW), stepDelayMs: 0, now: () => NOW, ...extra });
}

async function call(backend, method, path, body, init = {}) {
  const opts = { method, credentials: "include", ...init };
  if (body !== undefined) {
    opts.body = JSON.stringify(body);
    opts.headers = { "Content-Type": "application/json" };
  }
  const res = await backend.fetch(`${BASE}${path}`, opts);
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : null, headers: res.headers };
}

async function signup(backend, name, email, password = "correct-horse") {
  const r = await call(backend, "POST", "/auth/signup", { email, password, name });
  assert.equal(r.status, 201, JSON.stringify(r.body));
  return r.body;
}

async function login(backend, email, password = "stitch-demo") {
  const r = await call(backend, "POST", "/auth/login", { email, password });
  assert.equal(r.status, 200, JSON.stringify(r.body));
  return r.body;
}

async function createFamily(backend, name, person) {
  const r = await call(backend, "POST", "/families", { name, person });
  assert.equal(r.status, 201, JSON.stringify(r.body));
  return r.body;
}

function memoryForm(fields, photos = []) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) for (const v of value) form.append(key, v);
    else form.append(key, String(value));
  }
  for (const photo of photos) form.append("photos", photo, "photo.png");
  return form;
}

async function submit(backend, fid, fields, photos) {
  const res = await backend.fetch(`${BASE}/families/${fid}/memories`, {
    method: "POST",
    body: memoryForm(fields, photos),
    credentials: "include",
  });
  return { status: res.status, body: await res.json() };
}

/** A tiny SSE reader: yields { id, event, data } per message. */
async function* readSse(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const msg = {};
        for (const line of block.split("\n")) {
          const colon = line.indexOf(":");
          const key = line.slice(0, colon);
          const value = line.slice(colon + 1).replace(/^ /, "");
          if (key === "data") msg.data = JSON.parse(value);
          else msg[key] = value;
        }
        yield msg;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

async function openStream(backend, fid, runId, signal) {
  const res = await backend.fetch(`${BASE}/families/${fid}/runs/${runId}/stream`, {
    credentials: "include",
    signal,
  });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("content-type"), "text/event-stream");
  return { res, events: readSse(res.body) };
}

/** Read events until one named `stop` arrives (inclusive). */
async function readUntil(events, stop) {
  const seen = [];
  for (;;) {
    const { value, done } = await events.next();
    if (done) throw new Error(`stream ended before "${stop}"; saw ${seen.map((e) => e.event).join(",")}`);
    seen.push(value);
    if (value.event === stop) return seen;
  }
}

async function answer(backend, fid, runId, body) {
  return call(backend, "POST", `/families/${fid}/runs/${runId}/answer`, body);
}

/** Answer everything the run asks for with defaults and return every event. */
async function runToEnd(backend, fid, runId) {
  const { events } = await openStream(backend, fid, runId);
  const all = [];
  for await (const msg of events) {
    all.push(msg);
    if (msg.event === "question") assert.equal((await answer(backend, fid, runId, { kind: "answer", text: "At the church, with everyone." })).status, 204);
    if (msg.event === "props_found") assert.equal((await answer(backend, fid, runId, { kind: "props", props: [] })).status, 204);
    if (msg.event === "merge") assert.equal((await answer(backend, fid, runId, { kind: "merge", approve: true })).status, 204);
  }
  return all;
}

function fakeStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => void data.set(k, String(v)),
    removeItem: (k) => void data.delete(k),
  };
}

// ---------------------------------------------------------------------------
// Source rules

test("mockBackend.ts and demoSeed.ts only have type imports; index.ts composes them", () => {
  for (const file of ["mockBackend.ts", "demoSeed.ts"]) {
    const src = fs.readFileSync(new URL(`../mock/${file}`, import.meta.url), "utf8");
    const imports = src.split("\n").filter((l) => /^\s*import\s/.test(l));
    assert.ok(imports.length > 0, `${file} imports its types`);
    for (const line of imports) assert.match(line, /^import type /, `${file}: ${line}`);
    assert.doesNotMatch(src, /\brequire\(|\bimport\(/, `${file} has no dynamic imports`);
  }
  const index = fs.readFileSync(new URL("../mock/index.ts", import.meta.url), "utf8");
  assert.match(index, /from "\.\/mockBackend"/);
  assert.match(index, /from "\.\/demoSeed"/);
});

// ---------------------------------------------------------------------------
// Demo seed

test("buildDemoSeed builds the Riveras around the sample memory", () => {
  const seed = buildDemoSeed(SAMPLE, NOW);
  assert.equal(seed.families.length, 1);
  const family = seed.families[0];
  assert.equal(family.id, "sample_family");
  assert.equal(family.name, "The Riveras");

  const ids = seed.people.map((p) => p.id).sort();
  assert.deepEqual(ids, ["dad", "luis", "maya", "mom", "rosa", "sam"]);
  const byId = Object.fromEntries(seed.people.map((p) => [p.id, p]));
  assert.equal(byId.rosa.born, 1938);
  assert.equal(byId.luis.born, 1936);
  assert.equal(byId.dad.name, "David");
  assert.equal(byId.dad.born, 1962);
  assert.equal(byId.mom.name, "Ana");
  assert.equal(byId.maya.born, 1991);
  assert.equal(byId.sam.born, 1994);
  assert.deepEqual(byId.dad.character, SAMPLE.people.find((p) => p.id === "dad").character);
  assert.deepEqual(byId.mom.character, SAMPLE.people.find((p) => p.id === "mom").character);
  assert.deepEqual(byId.dad.parentIds, ["rosa", "luis"]);
  assert.deepEqual(byId.maya.parentIds, ["dad", "mom"]);
  assert.deepEqual(byId.sam.parentIds, ["dad", "mom"]);
  assert.deepEqual(byId.rosa.spouseIds, ["luis"]);
  assert.deepEqual(byId.dad.spouseIds, ["mom"]);
  for (const p of seed.people) assert.equal(p.familyId, "sample_family");

  // Relationships are consistent.
  for (const p of seed.people) {
    for (const id of [...(p.parentIds ?? []), ...(p.spouseIds ?? [])]) assert.ok(byId[id], `${p.id} -> ${id}`);
    for (const id of p.spouseIds ?? []) assert.ok(byId[id].spouseIds.includes(p.id), `spouse ${p.id} <-> ${id}`);
  }
  const colors = seed.people.map((p) => p.character.color.toLowerCase());
  assert.equal(new Set(colors).size, colors.length, "character colors are distinct");

  // Users and who claims whom.
  const david = seed.users.find((u) => u.email === "david@example.com");
  const ana = seed.users.find((u) => u.email === "ana@example.com");
  assert.equal(david.password, "stitch-demo");
  assert.equal(ana.password, "stitch-demo");
  assert.equal(byId.dad.userId, david.id);
  assert.equal(byId.mom.userId, ana.id);
  for (const id of ["rosa", "luis", "maya", "sam"]) assert.equal(byId[id].userId ?? null, null);
  assert.deepEqual(family.members, [
    { userId: david.id, role: "admin" },
    { userId: ana.id, role: "member" },
  ]);

  // Invite.
  assert.equal(family.invites.length, 1);
  assert.equal(family.invites[0].code, "riveras-demo");
  assert.equal(family.invites[0].createdBy, david.id);
  assert.equal(Date.parse(family.invites[0].expiresAt) - NOW.getTime(), 14 * 24 * 3600 * 1000);

  // Memories: the sample unchanged plus two lighter moments.
  assert.equal(seed.memories.length, 3);
  assert.deepEqual(seed.memories[0], SAMPLE.memory);
  const light = seed.memories.slice(1);
  for (const m of light) {
    assert.equal(m.familyId, "sample_family");
    assert.equal(m.screenplay, null);
    assert.equal(m.timeline, null);
    assert.equal(m.tellings.length, 1);
    for (const id of m.participantIds) assert.ok(byId[id]);
    assert.ok(m.participantIds.includes(m.tellings[0].authorId));
  }
  assert.deepEqual(
    light.map((m) => [m.title, m.year]),
    [
      ["Our first house", 1993],
      ["Maya's first bike", 1998],
    ],
  );

  // Questions.
  const momQ = seed.questions.find((q) => q.personId === "mom");
  assert.equal(momQ.text, "David told the story of the hymn books. What do you remember?");
  assert.equal(momQ.memoryId, SAMPLE.memory.id);
  assert.equal(momQ.status, "open");
  const dadQ = seed.questions.find((q) => q.personId === "dad");
  assert.equal(dadQ.memoryId ?? null, null);
  assert.equal(dadQ.status, "open");

  // The seed does not share objects with the sample.
  seed.memories[0].title = "changed";
  assert.equal(SAMPLE.memory.title, "How I met your mother");
});

// ---------------------------------------------------------------------------
// Accounts and families

test("signup, family, invite, join by claiming a person", async () => {
  const b = makeBackend();
  const me0 = await call(b, "GET", "/me");
  assert.equal(me0.status, 401);
  assert.equal(typeof me0.body.detail, "string");

  const created = await signup(b, "Mei", "Mei@Example.com");
  assert.equal(created.user.name, "Mei");
  assert.deepEqual(created.families, []);
  assert.equal("password" in created.user, false);

  const me = await call(b, "GET", "/me");
  assert.equal(me.status, 200);
  assert.equal(me.body.user.id, created.user.id);

  const fam = await createFamily(b, "The Maos", { name: "Mei", born: 1960 });
  assert.equal(fam.family.role, "admin");
  assert.equal(fam.family.personId, fam.person.id);
  assert.equal(fam.person.userId, created.user.id);
  assert.match(fam.person.character.color, /^#[0-9a-f]{6}$/i);
  const fid = fam.family.id;

  const me2 = await call(b, "GET", "/me");
  assert.deepEqual(me2.body.families, [{ id: fid, name: "The Maos", role: "admin", personId: fam.person.id }]);

  // Add an unclaimed relative to claim later.
  const kid = await call(b, "POST", `/families/${fid}/people`, { name: "Jun", born: 1990, parentIds: [fam.person.id] });
  assert.equal(kid.status, 201);
  assert.equal(kid.body.userId, null);

  const invite = await call(b, "POST", `/families/${fid}/invites`);
  assert.equal(invite.status, 201);
  assert.equal(invite.body.path, `/join/${invite.body.code}`);
  assert.equal(Date.parse(invite.body.expiresAt) - NOW.getTime(), 14 * 24 * 3600 * 1000);

  assert.equal((await call(b, "POST", "/auth/logout")).status, 204);
  assert.equal((await call(b, "GET", "/me")).status, 401);

  // Preview works signed out.
  const preview = await call(b, "GET", `/invites/${invite.body.code}`);
  assert.equal(preview.status, 200);
  assert.equal(preview.body.familyId, fid);
  assert.equal(preview.body.familyName, "The Maos");
  assert.deepEqual(preview.body.unclaimedPeople, [
    { id: kid.body.id, name: "Jun", born: 1990, color: kid.body.character.color },
  ]);

  // Joining needs a login.
  assert.equal((await call(b, "POST", `/invites/${invite.body.code}/join`, { personId: kid.body.id })).status, 401);

  const jun = await signup(b, "Jun", "jun@example.com");
  const bad = await call(b, "POST", `/invites/${invite.body.code}/join`, {});
  assert.equal(bad.status, 422);
  const both = await call(b, "POST", `/invites/${invite.body.code}/join`, {
    personId: kid.body.id,
    person: { name: "Jun" },
  });
  assert.equal(both.status, 422);

  const joined = await call(b, "POST", `/invites/${invite.body.code}/join`, { personId: kid.body.id });
  assert.equal(joined.status, 200);
  assert.deepEqual(joined.body.family, { id: fid, name: "The Maos", role: "member", personId: kid.body.id });
  assert.equal(joined.body.person.userId, jun.user.id);

  const tree = await call(b, "GET", `/families/${fid}/tree`);
  assert.equal(tree.status, 200);
  assert.equal(tree.body.familyName, "The Maos");
  assert.equal(tree.body.people.find((p) => p.id === kid.body.id).userId, jun.user.id);

  const again = await call(b, "POST", `/invites/${invite.body.code}/join`, { person: { name: "Jun again" } });
  assert.equal(again.status, 409);
  assert.equal(again.body.detail, "You're already in this family.");

  // The claimed person no longer shows in the preview.
  const preview2 = await call(b, "GET", `/invites/${invite.body.code}`);
  assert.deepEqual(preview2.body.unclaimedPeople, []);
});

test("joining by adding yourself with relationships", async () => {
  const b = makeBackend();
  await signup(b, "Lena", "lena@example.com");
  const preview = await call(b, "GET", "/invites/riveras-demo");
  assert.equal(preview.status, 200);
  assert.equal(preview.body.familyName, "The Riveras");
  assert.deepEqual(preview.body.unclaimedPeople.map((p) => p.id).sort(), ["luis", "maya", "rosa", "sam"]);

  const unknownRel = await call(b, "POST", "/invites/riveras-demo/join", {
    person: { name: "Lena", parentIds: ["nobody"] },
  });
  assert.equal(unknownRel.status, 422);
  assert.deepEqual(unknownRel.body.detail[0].loc, ["body", "person", "parentIds"]);

  const joined = await call(b, "POST", "/invites/riveras-demo/join", {
    person: { name: "Lena", born: 1990, spouseIds: ["sam"] },
  });
  assert.equal(joined.status, 200);
  assert.equal(joined.body.family.role, "member");
  const tree = await call(b, "GET", "/families/sample_family/tree");
  const sam = tree.body.people.find((p) => p.id === "sam");
  assert.ok(sam.spouseIds.includes(joined.body.person.id), "spouse link is two-way");
});

test("signup and login errors", async () => {
  const b = makeBackend();
  await signup(b, "Mei", "mei@example.com");
  await call(b, "POST", "/auth/logout");

  const dup = await call(b, "POST", "/auth/signup", { email: "MEI@example.com", password: "long-enough", name: "Mei" });
  assert.equal(dup.status, 409);
  assert.equal(dup.body.detail, "An account with that email already exists.");

  const short = await call(b, "POST", "/auth/signup", { email: "x@example.com", password: "short", name: "X" });
  assert.equal(short.status, 422);
  assert.deepEqual(short.body, {
    detail: [
      {
        loc: ["body", "password"],
        msg: "String should have at least 8 characters",
        type: "string_too_short",
      },
    ],
  });

  const missing = await call(b, "POST", "/auth/signup", { password: "long-enough" });
  assert.equal(missing.status, 422);
  assert.deepEqual(
    missing.body.detail.map((d) => d.loc),
    [
      ["body", "email"],
      ["body", "name"],
    ],
  );
  for (const d of missing.body.detail) assert.equal(d.type, "missing");

  const wrong = await call(b, "POST", "/auth/login", { email: "mei@example.com", password: "nope-nope" });
  assert.equal(wrong.status, 401);
  assert.equal(wrong.body.detail, "That email and password don't match.");

  const demo = await login(b, "DAVID@example.com");
  assert.deepEqual(demo.families, [{ id: "sample_family", name: "The Riveras", role: "admin", personId: "dad" }]);
});

test("cross-family reads are 404; member admin actions are 403; invite states", async () => {
  let clock = NOW;
  const b = makeBackend({ now: () => clock });
  await signup(b, "Mei", "mei@example.com");
  const fam = await createFamily(b, "The Maos", { name: "Mei" });
  const fid = fam.family.id;

  // Another family's ids, through either family, are 404.
  for (const path of [
    "/families/sample_family/memories/sample_hymn_books",
    `/families/${fid}/memories/sample_hymn_books`,
    "/families/sample_family/tree",
    "/families/sample_family/people/dad/book",
    `/families/${fid}/people/dad/book`,
    "/families/sample_family/threads",
    "/families/sample_family/props",
    "/families/no_such_family/tree",
  ]) {
    const r = await call(b, "GET", path);
    assert.equal(r.status, 404, path);
  }
  assert.equal((await call(b, "POST", "/families/sample_family/invites")).status, 404);

  // A member (Ana) cannot manage invites or members.
  await call(b, "POST", "/auth/logout");
  await login(b, "ana@example.com");
  const forbidden = await call(b, "POST", "/families/sample_family/invites");
  assert.equal(forbidden.status, 403);
  assert.equal((await call(b, "DELETE", "/families/sample_family/invites/riveras-demo")).status, 403);
  assert.equal((await call(b, "DELETE", "/families/sample_family/members/user_david")).status, 403);
  assert.equal((await call(b, "GET", "/families/sample_family/tree")).status, 200);
  const asMember = await call(b, "GET", "/families/sample_family");
  assert.equal(asMember.status, 200);
  assert.equal(asMember.body.members.length, 2);
  assert.deepEqual(asMember.body.invites, [], "members don't see invite codes");

  // The admin revokes; the preview is 404 after.
  await login(b, "david@example.com");
  const asAdmin = await call(b, "GET", "/families/sample_family");
  assert.deepEqual(
    asAdmin.body.invites.map((i) => i.code),
    ["riveras-demo"],
  );
  assert.equal((await call(b, "GET", `/families/${fid}`)).status, 404);
  const fresh = await call(b, "POST", "/families/sample_family/invites");
  assert.equal((await call(b, "DELETE", `/families/sample_family/invites/${fresh.body.code}`)).status, 204);
  assert.equal((await call(b, "GET", `/invites/${fresh.body.code}`)).status, 404);
  assert.equal((await call(b, "GET", "/invites/not-a-code")).status, 404);

  // Expired after 14 days.
  assert.equal((await call(b, "GET", "/invites/riveras-demo")).status, 200);
  clock = new Date(NOW.getTime() + 15 * 24 * 3600 * 1000);
  assert.equal((await call(b, "GET", "/invites/riveras-demo")).status, 404);
  clock = NOW;

  // Claiming someone already claimed.
  await signup(b, "Copy", "copy@example.com");
  const claimed = await call(b, "POST", "/invites/riveras-demo/join", { personId: "dad" });
  assert.equal(claimed.status, 409);
});

test("people edits and member removal follow the server rules", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");

  const bad = await call(b, "POST", "/families/sample_family/people", { name: "Tia", spouseIds: ["ghost"] });
  assert.equal(bad.status, 422);
  assert.deepEqual(bad.body.detail[0].loc, ["body", "spouseIds"]);

  const tia = await call(b, "POST", "/families/sample_family/people", {
    name: "Tia",
    born: 1940,
    parentIds: [],
    spouseIds: [],
  });
  assert.equal(tia.status, 201);
  const colors = (await call(b, "GET", "/families/sample_family/tree")).body.people.map((p) => p.character.color);
  assert.equal(new Set(colors).size, colors.length, "auto color is unused");

  const patched = await call(b, "PATCH", `/families/sample_family/people/${tia.body.id}`, { spouseIds: ["luis"] });
  assert.equal(patched.status, 200);
  const self = await call(b, "PATCH", `/families/sample_family/people/${tia.body.id}`, { parentIds: [tia.body.id] });
  assert.equal(self.status, 422);
  assert.equal((await call(b, "PATCH", "/families/sample_family/people/ghost", { name: "x" })).status, 404);

  // Last admin cannot remove themselves.
  const last = await call(b, "DELETE", "/families/sample_family/members/user_david");
  assert.equal(last.status, 409);

  // Removing Ana unlinks her person.
  assert.equal((await call(b, "DELETE", "/families/sample_family/members/user_ana")).status, 204);
  const tree = await call(b, "GET", "/families/sample_family/tree");
  assert.equal(tree.body.people.find((p) => p.id === "mom").userId, null);
  await login(b, "ana@example.com");
  assert.equal((await call(b, "GET", "/families/sample_family/tree")).status, 404);
});

test("unknown route 404 and wrong method 405", async () => {
  const b = makeBackend();
  assert.equal((await call(b, "GET", "/nope")).status, 404);
  assert.equal((await call(b, "GET", "/auth/login")).status, 405);
});

// ---------------------------------------------------------------------------
// Memories and the run stream

test("submitting a short memory runs the scripted studio with pauses", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");

  // Validation.
  const empty = await submit(b, "sample_family", { text: "  ", year: 2001 });
  assert.equal(empty.status, 422);
  assert.deepEqual(empty.body.detail[0].loc, ["body", "text"]);
  const badYear = await submit(b, "sample_family", { text: "hi", year: 1700 });
  assert.equal(badYear.status, 422);
  assert.deepEqual(badYear.body.detail[0].loc, ["body", "year"]);
  const notInt = await submit(b, "sample_family", { text: "hi", year: "nineteen" });
  assert.deepEqual(notInt.body.detail[0].loc, ["body", "year"]);
  const stranger = await submit(b, "sample_family", { text: "hi", year: 2001, participantIds: ["ghost"] });
  assert.equal(stranger.status, 422);
  assert.deepEqual(stranger.body.detail[0].loc, ["body", "participantIds"]);
  const json = await call(b, "POST", "/families/sample_family/memories", { text: "hi", year: 2001 });
  assert.equal(json.status, 422);

  const photo = new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });
  const sub = await submit(
    b,
    "sample_family",
    { text: "Sam learned to swim at the lake.", year: 2001, question: "A summer to remember", participantIds: ["sam"] },
    [photo],
  );
  assert.equal(sub.status, 201);
  assert.equal(sub.body.memoryId, null);
  const { runId } = sub.body;

  const { events } = await openStream(b, "sample_family", runId);
  const first = await readUntil(events, "question");
  assert.equal(first[0].event, "plan");
  assert.ok(first[0].data.todos.length > 3);
  assert.equal(first[0].data.todos[0].status, "in_progress");
  assert.deepEqual(
    first.map((e) => e.event),
    ["plan", "subagent_start", "subagent_end", "question"],
  );
  assert.equal(first[1].data.name, "interviewer");
  assert.equal(first.at(-1).data.text, FOLLOW_UP_QUESTION);
  assert.deepEqual(
    first.map((e) => e.id),
    ["1", "2", "3", "4"],
  );

  // Paused: wrong kinds are refused, an empty answer is invalid.
  const wrong = await answer(b, "sample_family", runId, { kind: "props", props: [] });
  assert.equal(wrong.status, 409);
  assert.equal(wrong.body.detail, "The studio isn't waiting for that.");
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: " " })).status, 422);
  assert.equal((await answer(b, "sample_family", runId, { kind: "nope" })).status, 422);
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "At the lake, with Maya." })).status, 204);
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "twice" })).status, 409);

  const second = await readUntil(events, "props_found");
  assert.ok(second.some((e) => e.event === "subagent_start" && e.data.name === "storyboarder"));
  const chips = second.at(-1).data.props;
  assert.equal(chips.length, 2, "the sample's one prop plus one new chip");
  const reused = chips.find((c) => c.id === "hymn_books");
  assert.equal(reused.reused, true);
  const fresh = chips.find((c) => c.id !== "hymn_books");
  assert.equal(fresh.reused, false);

  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "x" })).status, 409);
  const unknownChip = await answer(b, "sample_family", runId, { kind: "props", props: [{ id: "zzz", action: "keep" }] });
  assert.equal(unknownChip.status, 422);
  const reviewed = await answer(b, "sample_family", runId, {
    kind: "props",
    props: [
      { id: "hymn_books", action: "rename", name: "Blue hymnals" },
      { id: fresh.id, action: "remove" },
    ],
  });
  assert.equal(reviewed.status, 204);

  const rest = [];
  for await (const msg of events) rest.push(msg);
  const names = rest.map((e) => e.event);
  for (const name of ["compile_error", "frames", "scene_ready", "done"]) assert.ok(names.includes(name), name);
  assert.ok(names.indexOf("compile_error") < names.indexOf("frames"));
  assert.ok(names.indexOf("scene_ready") < names.indexOf("done"));
  assert.equal(names.at(-1), "done");
  const ce = rest.find((e) => e.event === "compile_error").data;
  assert.equal(ce.round, 1);
  assert.equal(ce.errors[0].code, "not_holding");
  const directorRuns = rest.filter((e) => e.event === "subagent_start" && e.data.name === "director");
  assert.equal(directorRuns.length, 2, "director runs again after the compile error");
  const frames = rest.find((e) => e.event === "frames").data.frames;
  assert.equal(frames.length, 3);
  for (const f of frames) {
    const url = b.mediaUrl("sample_family", f.mediaId);
    assert.match(url, /^data:image\/svg\+xml/);
    assert.ok(decodeURIComponent(url).includes(`${f.t}s`), "placeholder shows the frame time");
  }
  const ready = rest.find((e) => e.event === "scene_ready").data;
  assert.equal(ready.version, 1);
  const done = rest.at(-1).data;
  assert.equal(done.memoryId, ready.memoryId);
  const lastPlan = rest.filter((e) => e.event === "plan").at(-1).data.todos;
  assert.ok(lastPlan.every((t) => t.status === "completed"));

  // The new moment is in both participants' books and in threads, by year.
  const mem = await call(b, "GET", `/families/sample_family/memories/${ready.memoryId}`);
  assert.equal(mem.status, 200);
  const m = mem.body.memory;
  assert.equal(m.title, "A summer to remember");
  assert.equal(m.year, 2001);
  assert.equal(m.era, "2000s");
  assert.deepEqual(m.participantIds, ["dad", "sam"]);
  assert.equal(m.tellings.length, 1);
  assert.equal(m.tellings[0].authorId, "dad");
  assert.ok(m.tellings[0].text.includes("At the lake, with Maya."), "the follow-up answer joins the telling");
  assert.equal(m.tellings[0].photoIds.length, 1);
  assert.equal(m.screenplay.memoryId, m.id);
  assert.ok(m.timeline.tracks.length > 0);
  assert.ok(m.propIds.includes("hymn_books"));
  assert.ok(!m.propIds.includes(fresh.id), "removed chip is not saved");
  assert.equal(mem.body.props.find((p) => p.id === "hymn_books").name, "Blue hymnals");
  assert.deepEqual(mem.body.people.map((p) => p.id).sort(), ["dad", "sam"]);
  const photoUrl = b.mediaUrl("sample_family", m.tellings[0].photoIds[0]);
  assert.ok(photoUrl.startsWith("blob:") || photoUrl.startsWith("data:"));
  const media = await b.fetch(`${BASE}/families/sample_family/media/${m.tellings[0].photoIds[0]}`);
  assert.equal(media.status, 200);
  assert.equal(media.headers.get("content-type"), "image/png");

  for (const pid of ["dad", "sam"]) {
    const book = await call(b, "GET", `/families/sample_family/people/${pid}/book`);
    assert.equal(book.status, 200);
    const ids = book.body.memories.map((x) => x.id);
    assert.ok(ids.includes(m.id), `${pid}'s book has it`);
    const years = book.body.memories.map((x) => x.year);
    assert.deepEqual(years, [...years].sort((a, b2) => a - b2));
  }
  const dadBook = await call(b, "GET", "/families/sample_family/people/dad/book");
  assert.deepEqual(
    dadBook.body.memories.map((x) => x.year),
    [1988, 1993, 1998, 2001],
  );
  assert.ok(dadBook.body.sets.some((x) => x.id === "church_steps_1988"));

  const threads = await call(b, "GET", "/families/sample_family/threads");
  assert.equal(threads.status, 200);
  assert.deepEqual(
    threads.body.moments.map((x) => x.year),
    [1988, 1993, 1998, 2001],
  );
  const moment = threads.body.moments.find((x) => x.id === m.id);
  assert.deepEqual(moment.tellingAuthorIds, ["dad"]);
  assert.deepEqual(moment.participantIds, ["dad", "sam"]);
  assert.equal(threads.body.people.find((p) => p.id === "sam").color, "#2e8b8b");

  // The run is over: answering now is a conflict.
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "late" })).status, 409);
});

test("only the starter can stream or answer a run", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const sub = await submit(b, "sample_family", { text: "short one", year: 2000 });
  await login(b, "ana@example.com");
  const stream = await b.fetch(`${BASE}/families/sample_family/runs/${sub.body.runId}/stream`);
  assert.equal(stream.status, 404);
  assert.equal((await answer(b, "sample_family", sub.body.runId, { kind: "answer", text: "hi" })).status, 404);
  assert.equal((await call(b, "GET", "/families/sample_family/runs/nope/stream")).status, 404);
});

test("a second telling appends to the moment and bumps its version", async () => {
  const b = makeBackend();
  await login(b, "ana@example.com");
  const missing = await submit(b, "sample_family", { text: LONG_TEXT, year: 1988, memoryId: "ghost" });
  assert.equal(missing.status, 404);

  const before = (await call(b, "GET", "/families/sample_family/memories/first_house")).body.memory;
  const qs = await call(b, "GET", "/families/sample_family/questions");
  assert.deepEqual(
    qs.body.questions.map((q) => q.id),
    ["q_mom_hymn_books"],
  );

  const sub = await submit(b, "sample_family", {
    text: LONG_TEXT,
    year: 1993,
    question: "What do you remember?",
    memoryId: "first_house",
  });
  assert.equal(sub.status, 201);
  assert.equal(sub.body.memoryId, "first_house");
  const events = await runToEnd(b, "sample_family", sub.body.runId);
  assert.ok(!events.some((e) => e.event === "question"), "a long telling needs no follow-up");
  const ready = events.find((e) => e.event === "scene_ready").data;
  assert.equal(ready.memoryId, "first_house");
  assert.equal(ready.version, (before.version ?? 1) + 1);

  const after = (await call(b, "GET", "/families/sample_family/memories/first_house")).body.memory;
  assert.equal(after.tellings.length, before.tellings.length + 1);
  assert.equal(after.tellings.at(-1).authorId, "mom");
  assert.equal(after.tellings.at(-1).text, LONG_TEXT);
  assert.equal(after.version, (before.version ?? 1) + 1);
  assert.equal(after.title, "Our first house");
  const threads = await call(b, "GET", "/families/sample_family/threads");
  assert.equal(threads.body.moments.filter((x) => x.id === "first_house").length, 1);
  assert.equal(threads.body.moments.length, 3, "no new moment");

  // The hymn books question is answered by a telling of that memory.
  const sub2 = await submit(b, "sample_family", { text: LONG_TEXT, year: 1988, memoryId: "sample_hymn_books" });
  await runToEnd(b, "sample_family", sub2.body.runId);
  const qs2 = await call(b, "GET", "/families/sample_family/questions");
  assert.deepEqual(qs2.body.questions, []);
  const hymn = (await call(b, "GET", "/families/sample_family/memories/sample_hymn_books")).body.memory;
  assert.equal(hymn.version, 2);
  assert.equal(hymn.tellings.length, 3);
});

test("a story about an uncle pauses for a merge", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const sub = await submit(b, "sample_family", { text: `My Uncle came over. ${LONG_TEXT}`, year: 1975 });
  const { events } = await openStream(b, "sample_family", sub.body.runId);
  const seen = await readUntil(events, "merge");
  assert.ok(!seen.some((e) => e.event === "question"));
  const interviewerEnd = seen.findIndex((e) => e.event === "subagent_end" && e.data.name === "interviewer");
  assert.ok(interviewerEnd >= 0 && interviewerEnd < seen.length - 1, "merge comes after the interviewer");
  assert.equal(seen.at(-1).data.personId, "luis");
  assert.equal(seen.at(-1).data.candidateName, "Uncle Luis");
  assert.equal((await answer(b, "sample_family", sub.body.runId, { kind: "props", props: [] })).status, 409);
  assert.equal((await answer(b, "sample_family", sub.body.runId, { kind: "merge" })).status, 422);
  assert.equal((await answer(b, "sample_family", sub.body.runId, { kind: "merge", approve: true })).status, 204);
  await readUntil(events, "props_found");
  assert.equal((await answer(b, "sample_family", sub.body.runId, { kind: "props", props: [] })).status, 204);
  const rest = [];
  for await (const msg of events) rest.push(msg);
  const ready = rest.find((e) => e.event === "scene_ready").data;
  const memory = (await call(b, "GET", `/families/sample_family/memories/${ready.memoryId}`)).body.memory;
  assert.ok(memory.participantIds.includes("luis"), "an approved merge adds the person");
  assert.equal(memory.title, "A new memory");
  // The linker asks Luis for his side.
  assert.ok(memory.participantIds.length >= 2);
});

test("reconnecting replays the whole log; aborting closes the stream cleanly", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const sub = await submit(b, "sample_family", { text: "short again", year: 2005 });
  const runId = sub.body.runId;

  const one = await openStream(b, "sample_family", runId);
  const firstPass = await readUntil(one.events, "question");

  const two = await openStream(b, "sample_family", runId);
  const replay = await readUntil(two.events, "question");
  assert.deepEqual(replay, firstPass);

  // Abort the second connection while it waits on the paused run.
  const controller = new AbortController();
  const three = await openStream(b, "sample_family", runId, controller.signal);
  const replayed = await readUntil(three.events, "question");
  assert.equal(replayed.length, firstPass.length);
  const pending = three.events.next();
  controller.abort();
  const end = await pending;
  assert.equal(end.done, true, "the aborted stream ends without throwing");

  // An already-aborted signal rejects like fetch does.
  await assert.rejects(
    b.fetch(`${BASE}/families/sample_family/runs/${runId}/stream`, { signal: controller.signal }),
    (err) => err.name === "AbortError",
  );

  // The first connection still works and the run still finishes.
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "At home." })).status, 204);
  await readUntil(one.events, "props_found");
  await answer(b, "sample_family", runId, { kind: "props", props: [] });
  const tail = [];
  for await (const msg of one.events) tail.push(msg);
  assert.equal(tail.at(-1).event, "done");

  // A connection after the run ends gets the full log and then closes.
  const all = [];
  const four = await openStream(b, "sample_family", runId);
  for await (const msg of four.events) all.push(msg);
  assert.equal(all[0].event, "plan");
  assert.equal(all.at(-1).event, "done");
  assert.deepEqual(
    all.map((e) => Number(e.id)),
    all.map((_, i) => i + 1),
  );
});

test("a new family's first moment gets its own scene, props and set", async () => {
  const b = makeBackend();
  await signup(b, "Mei", "mei@example.com");
  const fam = await createFamily(b, "The Maos", { name: "Mei", born: 1960 });
  const fid = fam.family.id;
  const sub = await submit(b, fid, { text: `We took the bike to the river. ${LONG_TEXT}`, year: 1999, title: "The river" });
  const events = await runToEnd(b, fid, sub.body.runId);
  const chips = events.find((e) => e.event === "props_found").data.props;
  assert.ok(chips.every((c) => c.reused === false), "nothing is saved in a brand-new family yet");
  assert.ok(chips.some((c) => c.name === "bicycle"));
  const ready = events.find((e) => e.event === "scene_ready").data;
  const res = await call(b, "GET", `/families/${fid}/memories/${ready.memoryId}`);
  const m = res.body.memory;
  assert.equal(m.title, "The river");
  assert.deepEqual(m.participantIds, [fam.person.id]);
  assert.ok(m.screenplay.cast.includes(fam.person.id), "the teller is cast in the scene");
  assert.ok(m.timeline.tracks.some((t) => t.target.startsWith(`puppet:${fam.person.id}`)));
  assert.ok(res.body.props.some((p) => p.id === "hymn_books" && p.familyId === fid));
  assert.ok(res.body.sets.some((x) => x.familyId === fid));
  const props = await call(b, "GET", `/families/${fid}/props`);
  assert.ok(props.body.props.every((p) => p.familyId === fid));
  const rename = await call(b, "PATCH", `/families/${fid}/props/hymn_books`, { name: "Song books" });
  assert.equal(rename.status, 200);
  assert.equal(rename.body.name, "Song books");
  // The demo family's prop is untouched.
  await login(b, "david@example.com");
  const demoProps = await call(b, "GET", "/families/sample_family/props");
  assert.equal(demoProps.body.props.find((p) => p.id === "hymn_books").name, "hymn books");
});

test("questions and ask", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const qs = await call(b, "GET", "/families/sample_family/questions");
  assert.deepEqual(
    qs.body.questions.map((q) => q.id),
    ["q_dad_maya_born"],
  );
  const asked = await call(b, "POST", "/families/sample_family/ask", { query: "Who dropped the hymn books?" });
  assert.equal(asked.status, 200);
  assert.ok(asked.body.quotes.length >= 1 && asked.body.quotes.length <= 3);
  assert.ok(asked.body.quotes.every((q) => q.memoryId === "sample_hymn_books"));
  assert.equal(typeof asked.body.answer, "string");
  assert.match(asked.body.answer, /\.$/);
  const none = await call(b, "POST", "/families/sample_family/ask", { query: "spaceship" });
  assert.deepEqual(none.body.quotes, []);
  assert.equal((await call(b, "POST", "/families/sample_family/ask", { query: "" })).status, 422);
});

// ---------------------------------------------------------------------------
// Persistence

test("state persists through storage, ignores corrupt data and resets", async () => {
  const storage = fakeStorage();
  const a = makeBackend({ storage });
  const created = await signup(a, "Mei", "mei@example.com");
  await createFamily(a, "The Maos", { name: "Mei" });
  const saved = JSON.parse(storage.getItem(MOCK_STORAGE_KEY));
  assert.equal(saved.session, created.user.id);

  // A fresh backend (a page reload) restores the user and the session.
  const b = makeBackend({ storage });
  const me = await call(b, "GET", "/me");
  assert.equal(me.status, 200);
  assert.equal(me.body.user.email, "mei@example.com");
  assert.equal(me.body.families[0].name, "The Maos");
  await call(b, "POST", "/auth/logout");
  await login(b, "mei@example.com", "correct-horse");

  // Corrupt storage is ignored.
  const corrupt = fakeStorage({ [MOCK_STORAGE_KEY]: "{not json" });
  const c = makeBackend({ storage: corrupt });
  assert.equal((await call(c, "GET", "/me")).status, 401);
  await login(c, "david@example.com");
  const wrongShape = fakeStorage({ [MOCK_STORAGE_KEY]: JSON.stringify({ v: 1, users: "nope" }) });
  assert.equal((await call(makeBackend({ storage: wrongShape }), "GET", "/invites/riveras-demo")).status, 200);

  // Storage that throws never breaks the backend.
  const throwing = {
    getItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
    removeItem() {
      throw new Error("blocked");
    },
  };
  const d = makeBackend({ storage: throwing });
  await signup(d, "Zed", "zed@example.com");
  d.reset();
  assert.equal((await call(d, "GET", "/me")).status, 401);

  // reset() clears memory and storage.
  b.reset();
  assert.equal(storage.getItem(MOCK_STORAGE_KEY), null);
  assert.equal((await call(b, "GET", "/me")).status, 401);
  const relog = await call(b, "POST", "/auth/login", { email: "mei@example.com", password: "correct-horse" });
  assert.equal(relog.status, 401, "the signed-up user is gone after reset");
  await login(b, "david@example.com");
});

test("the seed loads lazily, once, and may be async", async () => {
  let loads = 0;
  const b = createMockBackend({
    seed: async () => {
      loads += 1;
      await new Promise((r) => setTimeout(r, 5));
      return buildDemoSeed(SAMPLE, NOW);
    },
    stepDelayMs: 0,
  });
  assert.equal(loads, 0);
  const results = await Promise.all([
    call(b, "GET", "/invites/riveras-demo"),
    call(b, "GET", "/invites/riveras-demo"),
  ]);
  assert.deepEqual(
    results.map((r) => r.status),
    [200, 200],
  );
  assert.equal(loads, 1);

  let failOnce = true;
  const flaky = createMockBackend({
    seed: () => {
      if (failOnce) {
        failOnce = false;
        throw new Error("offline");
      }
      return buildDemoSeed(SAMPLE, NOW);
    },
    stepDelayMs: 0,
  });
  assert.equal((await call(flaky, "GET", "/invites/riveras-demo")).status, 503);
  assert.equal((await call(flaky, "GET", "/invites/riveras-demo")).status, 200);
});

async function upload(backend, fid, file, field = "file") {
  const form = new FormData();
  if (file) form.append(field, file, file.name ?? "photo.png");
  const res = await backend.fetch(`${BASE}/families/${encodeURIComponent(fid)}/media`, {
    method: "POST",
    body: form,
    credentials: "include",
  });
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : null };
}

test("POST /families/{fid}/media stores a photo that mediaUrl and GET media serve", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const png = new File([new Uint8Array([137, 80, 78, 71, 1, 2, 3])], "hymns.png", { type: "image/png" });
  const r = await upload(b, "sample_family", png);
  assert.equal(r.status, 201, JSON.stringify(r.body));
  assert.deepEqual(Object.keys(r.body), ["id"]);
  const id = r.body.id;
  assert.ok(id && !id.startsWith("media/"), "a bare media id");

  const served = await b.fetch(`${BASE}/families/sample_family/media/${id}`, { credentials: "include" });
  assert.equal(served.status, 200);
  assert.equal(served.headers.get("content-type"), "image/png");
  assert.deepEqual(new Uint8Array(await served.arrayBuffer()), new Uint8Array([137, 80, 78, 71, 1, 2, 3]));

  const url = b.mediaUrl("sample_family", id);
  assert.ok(url.startsWith("blob:"), url);
  assert.equal(b.mediaUrl("sample_family", `media/${id}`), url, "the media/ prefix is accepted");
  assert.equal((await (await fetch(url)).arrayBuffer()).byteLength, 7, "the blob URL holds the bytes");
  assert.ok(b.mediaUrl("other_family", id).startsWith("data:"), "another family gets the placeholder");

  // The id is good for a prop's replacement photo, with or without the prefix.
  const patched = await call(b, "PATCH", "/families/sample_family/props/hymn_books", { photoId: `media/${id}` });
  assert.equal(patched.status, 200, JSON.stringify(patched.body));
  assert.equal(patched.body.art.body, `media/${id}`, "stored in the media/<id> form, as the real API does");
  assert.equal(patched.body.source, "photo");
  const bare = await call(b, "PATCH", "/families/sample_family/props/hymn_books", { photoId: id });
  assert.equal(bare.status, 200, JSON.stringify(bare.body));
  assert.equal(bare.body.art.body, `media/${id}`);
  assert.equal(b.mediaUrl("sample_family", bare.body.art.body), url, "the stored art resolves to the upload");

  const unknown = await call(b, "PATCH", "/families/sample_family/props/hymn_books", { photoId: "media/nope" });
  assert.equal(unknown.status, 400);
  assert.equal(unknown.body.detail, "Unknown photo");
});

test("an uploaded photo works as a replace_photo edit in the props review", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const png = new File([new Uint8Array([137, 80, 78, 71, 5])], "hymnal.png", { type: "image/png" });
  const { body } = await upload(b, "sample_family", png);
  const sub = await submit(b, "sample_family", { text: "Sam learned to swim at the lake that summer.", year: 2001 });
  assert.equal(sub.status, 201);
  const { runId } = sub.body;
  const { events } = await openStream(b, "sample_family", runId);
  await readUntil(events, "question");
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "At the lake." })).status, 204);
  await readUntil(events, "props_found");
  const unknown = await answer(b, "sample_family", runId, {
    kind: "props",
    props: [{ id: "hymn_books", action: "replace_photo", photoId: "nope" }],
  });
  assert.equal(unknown.status, 422, "a photo that is not this family's is refused");
  const ok = await answer(b, "sample_family", runId, {
    kind: "props",
    props: [{ id: "hymn_books", action: "replace_photo", photoId: body.id }],
  });
  assert.equal(ok.status, 204, JSON.stringify(ok.body));
  for await (const msg of events) if (msg.event === "done" || msg.event === "error") break;
  const props = await call(b, "GET", "/families/sample_family/props");
  const hymns = props.body.props.find((p) => p.id === "hymn_books");
  assert.equal(hymns.art.body, `media/${body.id}`);
  assert.equal(hymns.source, "photo");
  assert.ok(b.mediaUrl("sample_family", hymns.art.body).startsWith("blob:"));
});

test("media upload rules: members only, images only, not empty, not too big", async () => {
  const b = makeBackend();
  const png = () => new File([new Uint8Array([1, 2, 3])], "p.png", { type: "image/png" });

  // Signed out, then a non-member: the same answers as other family routes.
  assert.equal((await upload(b, "sample_family", png())).status, 401);
  await signup(b, "Mei", "mei@example.com");
  const outsider = await upload(b, "sample_family", png());
  assert.equal(outsider.status, 404);
  assert.equal(outsider.body.detail, "We couldn't find that family.");

  await login(b, "ana@example.com"); // a plain member may upload
  const text = await upload(b, "sample_family", new File(["hello"], "note.txt", { type: "text/plain" }));
  assert.equal(text.status, 415);
  assert.equal(text.body.detail, "Upload a JPEG, PNG, WebP, GIF or HEIC image");
  const untyped = await upload(b, "sample_family", new File(["hello"], "mystery"));
  assert.equal(untyped.status, 415);

  const empty = await upload(b, "sample_family", new File([], "empty.png", { type: "image/png" }));
  assert.equal(empty.status, 400);
  assert.equal(empty.body.detail, "The file is empty");

  assert.equal(MAX_MEDIA_BYTES, 20 * 1024 * 1024);
  const big = await upload(
    b,
    "sample_family",
    new File([new Uint8Array(MAX_MEDIA_BYTES + 1)], "big.jpg", { type: "image/jpeg" }),
  );
  assert.equal(big.status, 413);
  assert.equal(big.body.detail, "Photos must be 20 MB or smaller");
  const atLimit = await upload(
    b,
    "sample_family",
    new File([new Uint8Array(MAX_MEDIA_BYTES)], "ok.jpg", { type: "image/jpeg" }),
  );
  assert.equal(atLimit.status, 201);

  for (const type of ["image/jpeg", "image/webp", "image/gif", "image/heic", "IMAGE/PNG"]) {
    const ok = await upload(b, "sample_family", new File([new Uint8Array([9])], "x", { type }));
    assert.equal(ok.status, 201, type);
  }
  // The real API's ALLOWED_IMAGE_TYPES has no image/heif.
  assert.equal((await upload(b, "sample_family", new File([new Uint8Array([9])], "x", { type: "image/heif" }))).status, 415);

  const wrongField = await upload(b, "sample_family", png(), "photo");
  assert.equal(wrongField.status, 422);
  assert.deepEqual(wrongField.body.detail[0].loc, ["body", "file"]);
});

test("the API client's uploadMedia and getFamily work against the mock", async () => {
  const b = makeBackend();
  const client = createApiClient({ baseUrl: BASE, fetch: b.fetch, mediaUrl: b.mediaUrl });
  await client.login({ email: "david@example.com", password: "stitch-demo" });

  const family = await client.getFamily("sample_family");
  assert.equal(family.name, "The Riveras");
  assert.equal(family.members.length, 2);

  const { id } = await client.uploadMedia("sample_family", new Blob(["gif"], { type: "image/gif" }));
  assert.ok(client.mediaUrl("sample_family", id).startsWith("blob:"));

  try {
    await client.uploadMedia("sample_family", new Blob(["text"], { type: "text/plain" }));
    assert.fail("expected a 415");
  } catch (e) {
    assert.ok(isApiError(e));
    assert.equal(e.status, 415);
    assert.equal(e.detail, "Upload a JPEG, PNG, WebP, GIF or HEIC image");
  }
});

test("accepts Request and URL inputs", async () => {
  const b = makeBackend();
  const req = new Request(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "david@example.com", password: "stitch-demo" }),
  });
  assert.equal((await b.fetch(req)).status, 200);
  const res = await b.fetch(new URL(`${BASE}/me`));
  assert.equal(res.status, 200);
  assert.equal((await res.json()).user.name, "David");
});
