// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { buildDemoSeed } from "../mock/demoSeed.ts";
import {
  createMockBackend,
  FOLLOW_UP_QUESTION,
  mergeCandidateFor,
  mergeQuestion,
  KEPT_RUNS,
  MAX_MEDIA_BYTES,
  MAX_STORED_RUNS,
  MOCK_RUNS_KEY,
  MOCK_STORAGE_KEY,
  NOT_WAITING,
  STALE_RUN_MS,
  addNarration,
  pruneRuns,
  splitSentences,
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

  // As the API: opening the link again and claiming yourself is a no-op 200;
  // adding a second person of your own is a 409.
  const reclaim = await call(b, "POST", `/invites/${invite.body.code}/join`, { personId: kid.body.id });
  assert.equal(reclaim.status, 200);
  assert.equal(reclaim.body.person.id, kid.body.id);
  const again = await call(b, "POST", `/invites/${invite.body.code}/join`, { person: { name: "Jun again" } });
  assert.equal(again.status, 409);
  assert.equal(again.body.detail, "You already have a person in this family");
  const other = await call(b, "POST", `/invites/${invite.body.code}/join`, { personId: fam.person.id });
  assert.equal(other.status, 409);
  assert.equal(other.body.detail, "Someone has already claimed this person");
  const members = (await call(b, "GET", "/me")).body.families;
  assert.equal(members.length, 1, "joining again adds no second membership");

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

  // A bad relation is the API's 400 InvalidRelation, and a failed join leaves no membership.
  const unknownRel = await call(b, "POST", "/invites/riveras-demo/join", {
    person: { name: "Lena", parentIds: ["nobody"] },
  });
  assert.equal(unknownRel.status, 400);
  assert.equal(unknownRel.body.detail, "unknown person id(s) in this family: ['nobody']");
  assert.deepEqual((await call(b, "GET", "/me")).body.families, []);
  const blank = await call(b, "POST", "/invites/riveras-demo/join", { person: { name: "  " } });
  assert.equal(blank.status, 400);
  assert.equal(blank.body.detail, "A person needs a name");

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
  assert.equal(dup.body.detail, "An account with this email already exists");

  // The API's own 422 sentences for a bad email or a blank name, and the email is stored lowercased.
  const badEmail = await call(b, "POST", "/auth/signup", { email: "nope", password: "long-enough", name: "X" });
  assert.equal(badEmail.status, 422);
  assert.equal(badEmail.body.detail, "Enter a valid email");
  const blankName = await call(b, "POST", "/auth/signup", { email: "y@example.com", password: "long-enough", name: " " });
  assert.equal(blankName.status, 422);
  assert.equal(blankName.body.detail, "Enter your name");
  const upper = await call(b, "POST", "/auth/signup", { email: "  Zed@Example.COM ", password: "long-enough", name: "Zed" });
  assert.equal(upper.status, 201);
  assert.equal(upper.body.user.email, "zed@example.com");
  await call(b, "POST", "/auth/logout");

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
  assert.equal(wrong.body.detail, "Invalid email or password");

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
  assert.equal(bad.status, 400);
  assert.equal(bad.body.detail, "unknown person id(s) in this family: ['ghost']");

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
  assert.equal(self.status, 400);
  assert.equal(self.body.detail, "a person cannot be their own parent or spouse");
  const ghost = await call(b, "PATCH", "/families/sample_family/people/ghost", { name: "x" });
  assert.equal(ghost.status, 404);
  assert.equal(ghost.body.detail, "Person not found");

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

test("PATCH members role: promote, demote, no-op, 403, 404, 409 and stepping down", async () => {
  const b = makeBackend();
  const F = "/families/sample_family";
  const roles = (family) => Object.fromEntries(family.members.map((m) => [m.userId, m.role]));
  const get = async () => (await call(b, "GET", F)).body;

  // A member can't change roles, not even their own.
  await login(b, "ana@example.com");
  for (const [uid, role] of [["user_david", "member"], ["user_ana", "admin"]]) {
    const r = await call(b, "PATCH", `${F}/members/${uid}`, { role });
    assert.equal(r.status, 403, uid);
    assert.equal(r.body.detail, "Admins only");
  }

  await login(b, "david@example.com");
  const before = await get();
  assert.deepEqual(roles(before), { user_david: "admin", user_ana: "member" });

  // The only admin can't step down: 409, nothing changes.
  const last = await call(b, "PATCH", `${F}/members/user_david`, { role: "member" });
  assert.equal(last.status, 409);
  assert.equal(last.body.detail, "A family needs at least one admin");
  assert.deepEqual(roles(await get()), { user_david: "admin", user_ana: "member" });

  // Unknown member: 404. A bad or missing role: 422 on body.role.
  const ghost = await call(b, "PATCH", `${F}/members/user_ghost`, { role: "admin" });
  assert.equal(ghost.status, 404);
  assert.equal(ghost.body.detail, "Member not found");
  const bad = await call(b, "PATCH", `${F}/members/user_ana`, { role: "owner" });
  assert.equal(bad.status, 422);
  assert.deepEqual(bad.body.detail[0].loc, ["body", "role"]);
  const missing = await call(b, "PATCH", `${F}/members/user_ana`, {});
  assert.equal(missing.status, 422);
  assert.deepEqual(missing.body.detail[0].loc, ["body", "role"]);

  // Same role: a no-op that still answers with the family, as GET does.
  const same = await call(b, "PATCH", `${F}/members/user_ana`, { role: "member" });
  assert.equal(same.status, 200);
  assert.deepEqual(same.body, before);

  // Promote: the response is the admin's view of the family.
  const promoted = await call(b, "PATCH", `${F}/members/user_ana`, { role: "admin" });
  assert.equal(promoted.status, 200);
  assert.deepEqual(roles(promoted.body), { user_david: "admin", user_ana: "admin" });
  assert.deepEqual(promoted.body.invites.map((i) => i.code), ["riveras-demo"]);
  assert.deepEqual(promoted.body, await get());

  // Demote another admin.
  const demoted = await call(b, "PATCH", `${F}/members/user_ana`, { role: "member" });
  assert.equal(demoted.status, 200);
  assert.deepEqual(roles(demoted.body), { user_david: "admin", user_ana: "member" });
  assert.deepEqual(demoted.body, await get());

  // Step down while another admin remains: the member's view, with no invites.
  assert.equal((await call(b, "PATCH", `${F}/members/user_ana`, { role: "admin" })).status, 200);
  const stepped = await call(b, "PATCH", `${F}/members/user_david`, { role: "member" });
  assert.equal(stepped.status, 200);
  assert.deepEqual(roles(stepped.body), { user_david: "member", user_ana: "admin" });
  assert.deepEqual(stepped.body.invites, []);
  assert.deepEqual(stepped.body, await get());

  // The session and the admin checks follow: David is a member now.
  const me = await call(b, "GET", "/me");
  assert.equal(me.body.families.find((f) => f.id === "sample_family").role, "member");
  assert.equal((await call(b, "PATCH", `${F}/members/user_ana`, { role: "member" })).status, 403);
  assert.equal((await call(b, "POST", `${F}/invites`)).status, 403);

  // Ana, now the only admin, can't step down either.
  await login(b, "ana@example.com");
  assert.equal((await call(b, "PATCH", `${F}/members/user_ana`, { role: "member" })).status, 409);
});

test("the API client's setMemberRole works against the mock", async () => {
  const b = makeBackend();
  const client = createApiClient({ baseUrl: BASE, fetch: b.fetch, mediaUrl: b.mediaUrl });
  await client.login({ email: "david@example.com", password: "stitch-demo" });

  const family = await client.setMemberRole("sample_family", "user_ana", { role: "admin" });
  assert.equal(family.members.find((m) => m.userId === "user_ana").role, "admin");
  assert.deepEqual(await client.getFamily("sample_family"), family);

  await client.setMemberRole("sample_family", "user_ana", { role: "member" });
  try {
    await client.setMemberRole("sample_family", "user_david", { role: "member" });
    assert.fail("expected a 409");
  } catch (e) {
    assert.ok(isApiError(e));
    assert.equal(e.status, 409);
    assert.equal(e.detail, "A family needs at least one admin");
  }
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

  // Validation, with api/routes/memories.py's statuses and sentences.
  const empty = await submit(b, "sample_family", { text: "  ", year: 2001 });
  assert.equal(empty.status, 422);
  assert.equal(empty.body.detail, "Write the memory before submitting it");
  const missing = await submit(b, "sample_family", { year: 2001 });
  assert.equal(missing.status, 422);
  assert.deepEqual(missing.body.detail[0].loc, ["body", "text"]);
  const badYear = await submit(b, "sample_family", { text: "hi", year: 1700 });
  assert.equal(badYear.status, 422);
  assert.equal(badYear.body.detail, `Year must be between 1800 and ${NOW.getUTCFullYear() + 1}`);
  const nextYear = await submit(b, "sample_family", { text: "hi", year: NOW.getUTCFullYear() + 1 });
  assert.equal(nextYear.status, 202, "next year is still allowed");
  const notInt = await submit(b, "sample_family", { text: "hi", year: "nineteen" });
  assert.equal(notInt.status, 422);
  assert.deepEqual(notInt.body.detail[0].loc, ["body", "year"]);
  const stranger = await submit(b, "sample_family", { text: "hi", year: 2001, participantIds: ["ghost"] });
  assert.equal(stranger.status, 400);
  assert.equal(stranger.body.detail, "Every participant must be a person in this family");
  const ghostMoment = await submit(b, "sample_family", { text: "hi", year: 2001, memoryId: "ghost" });
  assert.equal(ghostMoment.status, 404);
  assert.equal(ghostMoment.body.detail, "Memory not found");
  const notPhoto = await submit(b, "sample_family", { text: "hi", year: 2001 }, [new Blob(["x"], { type: "text/plain" })]);
  assert.equal(notPhoto.status, 415);
  const json = await call(b, "POST", "/families/sample_family/memories", { text: "hi", year: 2001 });
  assert.equal(json.status, 422);

  const photo = new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });
  const sub = await submit(
    b,
    "sample_family",
    { text: "Sam learned to swim at the lake.", year: 2001, question: "A summer to remember", participantIds: ["sam"] },
    [photo],
  );
  assert.equal(sub.status, 202, "202 Accepted, as the API");
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
  assert.equal(wrong.body.detail, "That answer does not match what this run is waiting for");
  const blank = await answer(b, "sample_family", runId, { kind: "answer", text: " " });
  assert.equal(blank.status, 422);
  assert.equal(blank.body.detail, "An answer needs text");
  assert.equal((await answer(b, "sample_family", runId, { kind: "nope" })).status, 422);
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "At the lake, with Maya." })).status, 204);
  const twice = await answer(b, "sample_family", runId, { kind: "answer", text: "twice" });
  assert.equal(twice.status, 409);
  assert.equal(twice.body.detail, NOT_WAITING);

  const second = await readUntil(events, "props_found");
  assert.ok(second.some((e) => e.event === "subagent_start" && e.data.name === "storyboarder"));
  const chips = second.at(-1).data.props;
  assert.equal(chips.length, 2, "the sample's one prop plus one new chip");
  const reused = chips.find((c) => c.id === "hymn_books");
  assert.equal(reused.reused, true);
  const fresh = chips.find((c) => c.id !== "hymn_books");
  assert.equal(fresh.reused, false);

  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "x" })).status, 409);
  const noList = await answer(b, "sample_family", runId, { kind: "props" });
  assert.equal(noList.status, 422);
  assert.equal(noList.body.detail, "A props review needs the props list");
  const badAction = await answer(b, "sample_family", runId, { kind: "props", props: [{ id: "hymn_books", action: "burn" }] });
  assert.equal(badAction.status, 422);
  assert.deepEqual(badAction.body.detail[0].loc, ["body", "props", 0, "action"]);
  // As the scripted studio, an edit for a prop that isn't in the review is ignored.
  const reviewed = await answer(b, "sample_family", runId, {
    kind: "props",
    props: [
      { id: "zzz", action: "keep" },
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

test("any member can follow a run; only the starter can answer it", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const sub = await submit(b, "sample_family", { text: "short one", year: 2000 });
  await login(b, "ana@example.com");
  // api/routes/memories.py streams any run of the family to a member.
  const stream = await b.fetch(`${BASE}/families/sample_family/runs/${sub.body.runId}/stream`);
  assert.equal(stream.status, 200);
  await stream.body.cancel();
  const other = await answer(b, "sample_family", sub.body.runId, { kind: "answer", text: "hi" });
  assert.equal(other.status, 403);
  assert.equal(other.body.detail, "Only the person who started this run can answer");
  const nope = await call(b, "GET", "/families/sample_family/runs/nope/stream");
  assert.equal(nope.status, 404);
  assert.equal(nope.body.detail, "Run not found");
  assert.equal((await answer(b, "sample_family", "nope", { kind: "answer", text: "hi" })).status, 404);
  // Another family's run is a 404 too, even for its starter's family members.
  await signup(b, "Mei", "mei@example.com");
  const fam = await createFamily(b, "The Maos", { name: "Mei" });
  assert.equal((await call(b, "GET", `/families/${fam.family.id}/runs/${sub.body.runId}/stream`)).status, 404);
});

test("a second telling: no follow-up or review, the teller's narration track, the question closes", async () => {
  const b = makeBackend();
  await login(b, "ana@example.com");
  const missing = await submit(b, "sample_family", { text: LONG_TEXT, year: 1988, memoryId: "ghost" });
  assert.equal(missing.status, 404);
  // A moment with no scene yet can't take another telling (the studio's 409 NO_SCENE).
  const sceneless = await submit(b, "sample_family", { text: LONG_TEXT, year: 1993, memoryId: "first_house" });
  assert.equal(sceneless.status, 409);
  assert.equal(sceneless.body.detail, "This moment has no scene yet, so another telling cannot be added to it");

  const qs = await call(b, "GET", "/families/sample_family/questions");
  assert.deepEqual(
    qs.body.questions.map((q) => q.id),
    ["q_mom_hymn_books"],
  );

  // David tells a new moment; Ana adds her version of it.
  await login(b, "david@example.com");
  const first = await submit(b, "sample_family", { text: LONG_TEXT, year: 2003, title: "The lake", participantIds: ["mom"] });
  const firstEvents = await runToEnd(b, "sample_family", first.body.runId);
  const memoryId = firstEvents.find((e) => e.event === "done").data.memoryId;
  await login(b, "ana@example.com");
  const asked = (await call(b, "GET", "/families/sample_family/questions")).body.questions;
  assert.equal(asked[0].memoryId, memoryId, "newest question first");

  const short = "I remember the rain. He laughed the whole way home!";
  const sub = await submit(b, "sample_family", { text: short, year: 2003, question: asked[0].text, memoryId });
  assert.equal(sub.status, 202);
  assert.equal(sub.body.memoryId, memoryId);
  const events = await runToEnd(b, "sample_family", sub.body.runId);
  const names = events.map((e) => e.event);
  for (const pause of ["question", "props_found", "merge"]) assert.ok(!names.includes(pause), `no ${pause}`);
  assert.equal(names.at(-1), "done");
  const ready = events.find((e) => e.event === "scene_ready").data;
  assert.equal(ready.memoryId, memoryId);
  assert.equal(ready.version, 2);

  const after = (await call(b, "GET", `/families/sample_family/memories/${memoryId}`)).body.memory;
  assert.deepEqual(after.tellings.map((t) => t.authorId), ["dad", "mom"]);
  assert.equal(after.tellings[1].text, short);
  assert.equal(after.title, "The lake");
  const track = after.timeline.narrations.mom;
  assert.equal(track.authorId, "mom");
  assert.deepEqual(track.lines.map((l) => l.text), ["I remember the rain.", "He laughed the whole way home!"]);
  assert.deepEqual(track.captions.map((c) => c.text), ["I remember the rain.", "He laughed the whole way home!"]);
  assert.ok(track.lines.every((l) => l.t >= 0 && l.t < after.timeline.duration));
  const spLines = Object.values(after.screenplay.narrations.mom).flat();
  assert.equal(spLines.length, 2);
  const threads = await call(b, "GET", "/families/sample_family/threads");
  assert.deepEqual(threads.body.moments.find((x) => x.id === memoryId).tellingAuthorIds, ["dad", "mom"]);
  assert.ok(!(await call(b, "GET", "/families/sample_family/questions")).body.questions.some((q) => q.memoryId === memoryId));

  // Telling the hymn books again replaces Ana's telling in place and answers her question.
  const sub2 = await submit(b, "sample_family", { text: LONG_TEXT, year: 1988, memoryId: "sample_hymn_books" });
  await runToEnd(b, "sample_family", sub2.body.runId);
  assert.deepEqual((await call(b, "GET", "/families/sample_family/questions")).body.questions, []);
  const hymn = (await call(b, "GET", "/families/sample_family/memories/sample_hymn_books")).body.memory;
  assert.equal(hymn.version, 2);
  assert.deepEqual(hymn.tellings.map((t) => t.authorId), ["dad", "mom"]);
  assert.equal(hymn.tellings[1].text, LONG_TEXT);
  assert.equal(hymn.timeline.narrations.mom.lines[0].source, "telling:mom:s1");
});

test("addNarration and splitSentences", () => {
  assert.deepEqual(splitSentences("One. Two!  Three?\nFour"), ["One.", "Two!", "Three?", "Four"]);
  assert.deepEqual(splitSentences("   "), []);
  const memory = JSON.parse(JSON.stringify(SAMPLE.memory));
  addNarration(memory, "sam", "Hello there. Bye.");
  const track = memory.timeline.narrations.sam;
  assert.equal(track.lines.length, 2);
  assert.deepEqual(track.lines[0].words.map((w) => w.word), ["Hello", "there"]);
  assert.ok(track.lines[1].t > track.lines[0].t);
  assert.ok(memory.timeline.narrations.dad, "other tracks stay");
  assert.equal(memory.screenplay.narrations.sam.s1.length, 2);
  // The default narrator's retelling also becomes the default captions.
  addNarration(memory, "dad", "New words.");
  assert.deepEqual(memory.timeline.captions.map((c) => c.text), ["New words."]);
  // No scene: nothing happens.
  const bare = { ...memory, timeline: null, screenplay: null };
  addNarration(bare, "sam", "Hi.");
  assert.equal(bare.timeline, null);
});

test("a story about Grandpa Lucho asks, as the scripted studio, whether he is the Luis in the tree", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const sub = await submit(b, "sample_family", { text: `Grandpa Lucho sang the loudest. ${LONG_TEXT}`, year: 1975 });
  const runId = sub.body.runId;
  const { events } = await openStream(b, "sample_family", runId);
  // As api/seed/fake_runs.py: the interviewer, the follow-up, then the merge.
  const asked = await readUntil(events, "question");
  assert.deepEqual(asked.map((e) => e.event), ["plan", "subagent_start", "subagent_end", "question"]);
  assert.deepEqual(asked[1].data, { name: "interviewer", task: "Read the telling and find what is missing" });
  assert.deepEqual(asked[2].data, { name: "interviewer", summary: "One follow-up: who else was there." });
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "Everyone." })).status, 204);
  const seen = await readUntil(events, "merge");
  assert.deepEqual(seen.at(-1).data, {
    text: "Is Grandpa Lucho the Luis in your family tree?",
    personId: "luis",
    candidateName: "Luis",
  });
  assert.equal(seen.at(-1).data.text, mergeQuestion("Grandpa Lucho", "Luis"));
  assert.equal((await answer(b, "sample_family", runId, { kind: "props", props: [] })).status, 409);
  assert.equal((await answer(b, "sample_family", runId, { kind: "merge" })).status, 422);
  assert.equal((await answer(b, "sample_family", runId, { kind: "merge", approve: true })).status, 204);
  await readUntil(events, "props_found");
  assert.equal((await answer(b, "sample_family", runId, { kind: "props", props: [] })).status, 204);
  const rest = [];
  for await (const msg of events) rest.push(msg);
  // The end of the script, in the API's order: the plan completes, then scene_ready, then done.
  assert.deepEqual(rest.slice(-3).map((e) => e.event), ["plan", "scene_ready", "done"]);
  assert.ok(rest.at(-3).data.todos.every((t) => t.status === "completed"));
  const ready = rest.find((e) => e.event === "scene_ready").data;
  const memory = (await call(b, "GET", `/families/sample_family/memories/${ready.memoryId}`)).body.memory;
  assert.ok(memory.participantIds.includes("luis"), "an approved merge adds the person");
  assert.equal(memory.title, "A new memory");
});

test("the merge is checked on the telling with the follow-up answer in it", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const sub = await submit(b, "sample_family", { text: LONG_TEXT, year: 1975 });
  const runId = sub.body.runId;
  const { events } = await openStream(b, "sample_family", runId);
  await readUntil(events, "question");
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "Grandpa Lucho drove." })).status, 204);
  const seen = await readUntil(events, "merge");
  assert.equal(seen.at(-1).data.text, "Is Grandpa Lucho the Luis in your family tree?");
});

test("no merge for a lowercase title, a name that is someone's own, or a rejected one", async () => {
  for (const text of ["Grandma and Grandpa were there.", "Grandma Rosa sang.", "my uncle Lucho came."]) {
    const b = makeBackend();
    await login(b, "david@example.com");
    const sub = await submit(b, "sample_family", { text: `${text} ${LONG_TEXT}`, year: 1975 });
    const all = await runToEnd(b, "sample_family", sub.body.runId);
    assert.ok(!all.some((e) => e.event === "merge"), `no merge for "${text}"`);
    assert.equal(all.filter((e) => e.event === "question").length, 1, "the follow-up is always asked");
  }
});

test("mergeCandidateFor follows fake_runs.merge_candidate", () => {
  const people = [
    { id: "grandpa_joseph", name: "Joseph Mao", parentIds: [] },
    { id: "grandma_rosa", name: "Rosa Mao", parentIds: [] },
    { id: "dad", name: "David Mao", parentIds: ["grandpa_joseph", "grandma_rosa"] },
    { id: "lily", name: "Lily Mao", parentIds: ["dad"] },
    { id: "luis", name: "Luis Diaz", parentIds: [] },
  ];
  const found = mergeCandidateFor(people, "dad", [], "Grandpa Joe was there.");
  assert.deepEqual(found && [found.said, found.person.id], ["Grandpa Joe", "grandpa_joseph"]);
  // The teller and the people already named are never asked about.
  assert.equal(mergeCandidateFor(people, "grandpa_joseph", [], "Grandpa Joe was there."), null);
  assert.equal(mergeCandidateFor(people, "dad", ["grandpa_joseph"], "Grandpa Joe was there."), null);
  // A grandparent title needs someone with a child: Lily is nobody's grandma.
  assert.equal(mergeCandidateFor(people, "dad", [], "Grandma Lil came."), null);
  // A gendered title never maps to a record that says the other gender.
  assert.equal(mergeCandidateFor(people, "dad", [], "Grandma Jo came."), null);
  // Luis has no child here, so "Grandpa Lu" is nobody; "Uncle Lu" is him.
  assert.equal(mergeCandidateFor(people, "dad", [], "Grandpa Lu came."), null);
  assert.equal(mergeCandidateFor(people, "dad", [], "Uncle Lu came.")?.person.id, "luis");
  // Lowercase is not a name; someone's own first name is that person, nothing to ask.
  assert.equal(mergeCandidateFor(people, "dad", [], "grandpa Joe and Grandpa joe."), null);
  assert.equal(mergeCandidateFor(people, "dad", [], "Grandma Rosa sang."), null);
  // Only a unique match asks.
  const twins = [...people, { id: "josie", name: "Josie Mao", parentIds: [] }];
  assert.equal(mergeCandidateFor(twins, "dad", [], "Cousin Jo came."), null);
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

test("a second telling never pauses, even when it is short and names an uncle", async () => {
  const b = makeBackend();
  await login(b, "ana@example.com");
  // Under SHORT_TELLING_WORDS and naming an uncle: a new memory would ask and offer a merge.
  const text = "My uncle sang loudest. The books flew everywhere!";
  const sub = await submit(b, "sample_family", { text, year: 1988, memoryId: "sample_hymn_books" });
  assert.equal(sub.status, 202);
  const { events } = await openStream(b, "sample_family", sub.body.runId);
  const all = [];
  for await (const msg of events) all.push(msg);
  const names = all.map((e) => e.event);
  for (const pause of ["question", "props_found", "merge"]) assert.ok(!names.includes(pause), `no ${pause}`);
  assert.deepEqual(names.filter((n) => n === "scene_ready" || n === "done"), ["scene_ready", "done"]);
  assert.equal(all.at(-1).data.memoryId, "sample_hymn_books");
  // Nothing is waiting: an answer is refused with the API's sentence.
  const late = await answer(b, "sample_family", sub.body.runId, { kind: "answer", text: "hi" });
  assert.equal(late.status, 409);
  assert.equal(late.body.detail, NOT_WAITING);
  const hymn = (await call(b, "GET", "/families/sample_family/memories/sample_hymn_books")).body.memory;
  assert.equal(hymn.tellings.find((t) => t.authorId === "mom").text, text);
  assert.deepEqual(
    hymn.timeline.narrations.mom.lines.map((l) => l.text),
    ["My uncle sang loudest.", "The books flew everywhere!"],
  );
  assert.ok(!hymn.participantIds.includes("luis"), "no merge was offered, so nobody was added");
});

// ---------------------------------------------------------------------------
// Runs survive a reload (stored next to the state, like the API keeps them)

function storedRunList(storage) {
  const raw = storage.getItem(MOCK_RUNS_KEY);
  return raw ? JSON.parse(raw).runs : [];
}

/** Every event the stream sends until it closes. */
async function drain(events) {
  const out = [];
  for await (const msg of events) out.push(msg);
  return out;
}

function assertOneLog(all) {
  assert.deepEqual(
    all.map((e) => Number(e.id)),
    all.map((_, i) => i + 1),
    "one log, numbered from 1 with nothing twice",
  );
}

test("a reload reattaches a paused run: it replays, the pause is still pending, and answering goes on to done", async () => {
  const storage = fakeStorage();
  const a = makeBackend({ storage });
  await login(a, "david@example.com");
  const sub = await submit(a, "sample_family", { text: "Sam learned to swim at the lake.", year: 2001, title: "Swimming" });
  const runId = sub.body.runId;
  const one = await openStream(a, "sample_family", runId);
  const asked = await readUntil(one.events, "question");
  await one.events.return();
  const saved = storedRunList(storage).find((r) => r.id === runId);
  assert.equal(saved.waiting, "answer");
  assert.equal(saved.events.length, asked.length);
  assert.equal(saved.finished, false);

  // The page reloads: a new backend over the same storage.
  const b = makeBackend({ storage });
  const two = await openStream(b, "sample_family", runId);
  assert.deepEqual(await readUntil(two.events, "question"), asked, "the feed replays");
  // Still paused on the follow-up: the API's statuses and sentences hold.
  const wrong = await answer(b, "sample_family", runId, { kind: "props", props: [] });
  assert.equal(wrong.status, 409);
  assert.equal(wrong.body.detail, "That answer does not match what this run is waiting for");
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "Maya held my hand." })).status, 204);
  const reviewing = await readUntil(two.events, "props_found");
  assert.ok(!reviewing.some((e) => e.event === "question"), "the question is not asked again");

  // Reloaded again while the props are up; this time the answer comes first,
  // before any stream (the page posts what the teller chose).
  const c = makeBackend({ storage });
  await login(c, "ana@example.com");
  const notOwner = await answer(c, "sample_family", runId, { kind: "props", props: [] });
  assert.equal(notOwner.status, 403);
  assert.equal(notOwner.body.detail, "Only the person who started this run can answer");
  await login(c, "david@example.com");
  assert.equal((await answer(c, "sample_family", runId, { kind: "props", props: [] })).status, 204);
  const twice = await answer(c, "sample_family", runId, { kind: "props", props: [] });
  assert.equal(twice.status, 409);
  assert.equal(twice.body.detail, NOT_WAITING);
  const all = await drain((await openStream(c, "sample_family", runId)).events);
  assertOneLog(all);
  const names = all.map((e) => e.event);
  for (const once of ["question", "props_found", "scene_ready", "done"]) {
    assert.equal(names.filter((n) => n === once).length, 1, `one ${once}`);
  }
  assert.equal(names.at(-1), "done");

  // The backend that lost the run catches up from storage and closes its stream.
  b.sync();
  const tail = await drain(two.events);
  assert.equal(tail.at(-1).event, "done");
  assert.equal(reviewing.length + asked.length + tail.length, all.length);

  // The moment was written once, with the follow-up answer in the telling.
  const moments = (await call(c, "GET", "/families/sample_family/threads")).body.moments.filter((m) => m.title === "Swimming");
  assert.equal(moments.length, 1);
  const memory = (await call(c, "GET", `/families/sample_family/memories/${moments[0].id}`)).body.memory;
  assert.ok(memory.tellings[0].text.includes("Maya held my hand."));
  assert.equal(all.at(-1).data.memoryId, memory.id);
});

test("a run cut off between steps resumes from where it stopped, and the old page load stops", async () => {
  const storage = fakeStorage();
  const a = makeBackend({ storage, stepDelayMs: 15 });
  await login(a, "david@example.com");
  const sub = await submit(a, "sample_family", { text: LONG_TEXT, year: 2003, title: "The lake" });
  const runId = sub.body.runId;
  const one = await openStream(a, "sample_family", runId);
  const early = await readUntil(one.events, "subagent_end");
  await one.events.return();

  // A second page load takes the run over; the first is still "open" and
  // notices at its next step that it no longer drives the run.
  const b = makeBackend({ storage });
  const all = await runToEnd(b, "sample_family", runId);
  assert.deepEqual(all.slice(0, early.length), early, "the steps already done replay as they were");
  assertOneLog(all);
  const names = all.map((e) => e.event);
  assert.equal(names.filter((n) => n === "question").length, 1, "the follow-up is asked once");
  assert.equal(names.filter((n) => n === "props_found").length, 1);
  assert.equal(names.filter((n) => n === "scene_ready").length, 1);
  assert.equal(names.at(-1), "done");

  await new Promise((resolve) => setTimeout(resolve, 60));
  const saved = storedRunList(storage).find((r) => r.id === runId);
  assert.equal(saved.finished, true);
  assert.equal(saved.events.length, all.length, "the old page load added nothing after it lost the run");
  const moments = (await call(b, "GET", "/families/sample_family/threads")).body.moments.filter((m) => m.title === "The lake");
  assert.equal(moments.length, 1);
});

test("a tab that took a run over and then closed hands it back to the tab still following it", async () => {
  const storage = fakeStorage();
  const a = makeBackend({ storage, stepDelayMs: 15 });
  await login(a, "david@example.com");
  const sub = await submit(a, "sample_family", { text: LONG_TEXT, year: 2003, title: "Two tabs" });
  const runId = sub.body.runId;
  const first = await openStream(a, "sample_family", runId);
  const seen = await readUntil(first.events, "subagent_start");

  // A second tab opens the same run and drives it for a few steps.
  const b = makeBackend({ storage, stepDelayMs: 15 });
  const abort = new AbortController();
  const second = await openStream(b, "sample_family", runId, abort.signal);
  await readUntil(second.events, "question");
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "Everyone." })).status, 204);
  await readUntil(second.events, "plan");

  // Then it closes: pagehide lets go of the run, and the closed tab's
  // script stops. The first tab hears the write (the storage event).
  b.release();
  abort.abort();
  assert.equal(storedRunList(storage).find((r) => r.id === runId).owner, "", "let go of");
  b.release(); // Nothing left to let go of: a no-op.
  a.sync();
  assert.notEqual(storedRunList(storage).find((r) => r.id === runId).owner, "", "the first tab drives it again");

  // The first tab's feed carries on to the props review and to done.
  seen.push(...(await readUntil(first.events, "props_found")));
  assert.equal((await answer(a, "sample_family", runId, { kind: "props", props: [] })).status, 204);
  const all = [...seen, ...(await drain(first.events))];
  assertOneLog(all);
  const names = all.map((e) => e.event);
  for (const once of ["props_found", "scene_ready", "done"]) {
    assert.equal(names.filter((n) => n === once).length, 1, `one ${once}`);
  }
  assert.equal(names.at(-1), "done");
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(storedRunList(storage).find((r) => r.id === runId).events.length, all.length);
});

test("a follower takes over a driver that went quiet without letting go, but not a live one", async () => {
  const storage = fakeStorage();
  let clock = NOW.getTime();
  const now = () => new Date(clock);
  const a = makeBackend({ storage, stepDelayMs: 15, now });
  await login(a, "david@example.com");
  const sub = await submit(a, "sample_family", { text: LONG_TEXT, year: 2003, title: "Quiet" });
  const runId = sub.body.runId;
  const first = await openStream(a, "sample_family", runId);
  const seen = await readUntil(first.events, "subagent_start");

  // A second tab takes the run over, then its timers stall (a frozen or
  // crashed tab): its next step is far off, and it never lets go.
  const b = makeBackend({ storage, stepDelayMs: 400, now });
  await openStream(b, "sample_family", runId);
  const owner = storedRunList(storage).find((r) => r.id === runId).owner;

  a.checkRuns();
  assert.equal(storedRunList(storage).find((r) => r.id === runId).owner, owner, "a driver that just wrote is left alone");
  clock += STALE_RUN_MS + 1;
  a.checkRuns();
  assert.notEqual(storedRunList(storage).find((r) => r.id === runId).owner, owner, "a quiet driver is taken over");

  seen.push(...(await readUntil(first.events, "question")));
  assert.equal((await answer(a, "sample_family", runId, { kind: "answer", text: "Everyone." })).status, 204);
  seen.push(...(await readUntil(first.events, "props_found")));
  assert.equal((await answer(a, "sample_family", runId, { kind: "props", props: [] })).status, 204);
  const all = [...seen, ...(await drain(first.events))];
  assertOneLog(all);
  assert.equal(all.at(-1).event, "done");
  // The stalled tab wakes up, sees it lost the run and adds nothing.
  await new Promise((resolve) => setTimeout(resolve, 450));
  assert.equal(storedRunList(storage).find((r) => r.id === runId).events.length, all.length);
});

test("a finished run reloads to its whole log; other families and reset still hide it", async () => {
  const storage = fakeStorage();
  const a = makeBackend({ storage });
  await login(a, "ana@example.com");
  const sub = await submit(a, "sample_family", { text: LONG_TEXT, year: 1988, memoryId: "sample_hymn_books" });
  const first = await runToEnd(a, "sample_family", sub.body.runId);

  const b = makeBackend({ storage });
  const again = await drain((await openStream(b, "sample_family", sub.body.runId)).events);
  assert.deepEqual(again, first, "the same log, then the stream closes");
  const late = await answer(b, "sample_family", sub.body.runId, { kind: "answer", text: "hi" });
  assert.equal(late.status, 409);
  assert.equal(late.body.detail, NOT_WAITING);

  // Another family's member gets the API's 404 for it.
  await signup(b, "Mei", "mei@example.com");
  const fam = await createFamily(b, "The Maos", { name: "Mei" });
  const other = await call(b, "GET", `/families/${fam.family.id}/runs/${sub.body.runId}/stream`);
  assert.equal(other.status, 404);
  assert.equal(other.body.detail, "Run not found");

  b.reset();
  assert.equal(storage.getItem(MOCK_RUNS_KEY), null, "reset clears the runs too");
  const c = makeBackend({ storage });
  await login(c, "ana@example.com");
  assert.equal((await call(c, "GET", `/families/sample_family/runs/${sub.body.runId}/stream`)).status, 404);
});

test("stored runs stay bounded: old finished runs go, a paused one stays", async () => {
  const storage = fakeStorage();
  const a = makeBackend({ storage });
  await login(a, "ana@example.com");
  const paused = (await submit(a, "sample_family", { text: "A short one.", year: 1990 })).body.runId;
  const pausedStream = await openStream(a, "sample_family", paused);
  await readUntil(pausedStream.events, "question");
  await pausedStream.events.return();
  const done = [];
  for (let i = 0; i < KEPT_RUNS + 2; i++) {
    const sub = await submit(a, "sample_family", { text: `${LONG_TEXT} Take ${i}.`, year: 1988, memoryId: "sample_hymn_books" });
    await runToEnd(a, "sample_family", sub.body.runId);
    done.push(sub.body.runId);
  }
  const ids = storedRunList(storage).map((r) => r.id);
  assert.deepEqual(ids, [paused, ...done.slice(2)], "the two oldest finished runs are gone; the paused one stays");

  const b = makeBackend({ storage });
  await login(b, "ana@example.com");
  assert.equal((await call(b, "GET", `/families/sample_family/runs/${done[0]}/stream`)).status, 404);
  assert.equal(
    (await drain((await openStream(b, "sample_family", done.at(-1))).events)).at(-1).event,
    "done",
  );
  const replay = await readUntil((await openStream(b, "sample_family", paused)).events, "question");
  assert.equal(replay.at(-1).data.text, FOLLOW_UP_QUESTION);
  assert.equal((await answer(b, "sample_family", paused, { kind: "answer", text: "At home." })).status, 204);

  // pruneRuns on its own: never more than MAX_STORED_RUNS, oldest first out.
  const open = Array.from({ length: MAX_STORED_RUNS + 5 }, (_, i) => ({ id: i, finished: false }));
  assert.deepEqual(pruneRuns(open).map((r) => r.id), open.slice(5).map((r) => r.id));
  const mixed = Array.from({ length: KEPT_RUNS + 3 }, (_, i) => ({ id: i, finished: i !== 1 }));
  assert.deepEqual(
    pruneRuns(mixed).map((r) => r.id),
    [1, ...mixed.slice(3).map((r) => r.id)],
  );
});

test("unreadable stored runs are ignored", async () => {
  const storage = fakeStorage({ [MOCK_RUNS_KEY]: "{nope" });
  const a = makeBackend({ storage });
  await login(a, "david@example.com");
  assert.equal((await call(a, "GET", "/families/sample_family/runs/run_x/stream")).status, 404);
  const sub = await submit(a, "sample_family", { text: LONG_TEXT, year: 2003 });
  assert.equal(sub.status, 202);
  const first = await runToEnd(a, "sample_family", sub.body.runId);
  assert.deepEqual(storedRunList(storage).map((r) => r.id), [sub.body.runId], "rewritten as a valid list");

  // A stored run of the wrong shape is skipped; the good one still loads.
  const good = storedRunList(storage)[0];
  storage.setItem(
    MOCK_RUNS_KEY,
    JSON.stringify({ v: 1, runs: [{ ...good, id: "run_bad", cursor: "three" }, { id: "run_worse" }, good] }),
  );
  const b = makeBackend({ storage });
  assert.equal((await call(b, "GET", "/families/sample_family/runs/run_bad/stream")).status, 404);
  assert.equal((await call(b, "GET", "/families/sample_family/runs/run_worse/stream")).status, 404);
  assert.deepEqual(await drain((await openStream(b, "sample_family", sub.body.runId)).events), first);
  // A run of the wrong version is ignored.
  const v2 = fakeStorage({ [MOCK_STORAGE_KEY]: storage.getItem(MOCK_STORAGE_KEY), [MOCK_RUNS_KEY]: JSON.stringify({ v: 2, runs: [good] }) });
  assert.equal((await call(makeBackend({ storage: v2 }), "GET", `/families/sample_family/runs/${sub.body.runId}/stream`)).status, 404);
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
  assert.equal(sub.status, 202);
  const { runId } = sub.body;
  const { events } = await openStream(b, "sample_family", runId);
  await readUntil(events, "question");
  assert.equal((await answer(b, "sample_family", runId, { kind: "answer", text: "At the lake." })).status, 204);
  await readUntil(events, "props_found");
  // As the scripted studio: a photo that is not this family's is ignored, the right one is used.
  const ok = await answer(b, "sample_family", runId, {
    kind: "props",
    props: [
      { id: "hymn_books", action: "replace_photo", photoId: "nope" },
      { id: "hymn_books", action: "replace_photo", photoId: body.id },
    ],
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
  assert.equal(outsider.body.detail, "Not found");

  await login(b, "ana@example.com"); // a plain member may upload
  const text = await upload(b, "sample_family", new File(["hello"], "note.txt", { type: "text/plain" }));
  assert.equal(text.status, 415);
  assert.equal(text.body.detail, "Upload a JPEG, PNG, WebP, GIF or HEIC/HEIF image");
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

  // api/routes/props.py ALLOWED_IMAGE_TYPES, HEIF included.
  for (const type of ["image/jpeg", "image/webp", "image/gif", "image/heic", "image/heif", "IMAGE/PNG"]) {
    const ok = await upload(b, "sample_family", new File([new Uint8Array([9])], "x", { type }));
    assert.equal(ok.status, 201, type);
  }
  assert.equal((await upload(b, "sample_family", new File([new Uint8Array([9])], "x", { type: "image/svg+xml" }))).status, 415);

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
    assert.equal(e.detail, "Upload a JPEG, PNG, WebP, GIF or HEIC/HEIF image");
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

// ---------------------------------------------------------------------------
// Parity with the real API (api/routes/families.py, api/db/people.py,
// api/routes/auth.py, api/routes/props.py): statuses, shapes and details
// found different by a side-by-side run against `python -m seed.serve`.

test("parity: error details are the API's own words", async () => {
  const b = makeBackend();
  const F = "/families/sample_family";
  assert.equal((await call(b, "GET", "/me")).body.detail, "Not signed in");
  assert.equal((await call(b, "POST", "/auth/login", { email: "nope", password: "whatever1" })).body.detail, "Invalid email or password");
  await login(b, "david@example.com");
  assert.equal((await call(b, "GET", "/families/nope")).body.detail, "Not found");
  assert.equal((await call(b, "GET", "/families/nope/tree")).body.detail, "Not found");
  assert.equal((await call(b, "GET", `${F}/memories/nope`)).body.detail, "Not found");
  assert.equal((await call(b, "GET", `${F}/people/nope/book`)).body.detail, "Not found");
  const revokeGhost = await call(b, "DELETE", `${F}/invites/nope`);
  assert.equal(revokeGhost.status, 404);
  assert.equal(revokeGhost.body.detail, "Invite not found or expired");
  assert.equal((await call(b, "GET", "/invites/nope")).body.detail, "Invite not found or expired");
  const removeGhost = await call(b, "DELETE", `${F}/members/user_ghost`);
  assert.equal(removeGhost.status, 404);
  assert.equal(removeGhost.body.detail, "Member not found");
  const last = await call(b, "DELETE", `${F}/members/user_david`);
  assert.equal(last.status, 409);
  assert.equal(last.body.detail, "A family needs at least one admin");
  await login(b, "ana@example.com");
  for (const [method, path, body] of [
    ["POST", `${F}/invites`],
    ["DELETE", `${F}/invites/riveras-demo`],
    ["DELETE", `${F}/members/user_david`],
    ["PATCH", `${F}/members/user_david`, { role: "member" }],
  ]) {
    const r = await call(b, method, path, body);
    assert.equal(r.status, 403, path);
    assert.equal(r.body.detail, "Admins only", path);
  }
});

test("parity: revoking an invite twice is a 204 both times", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const inv = await call(b, "POST", "/families/sample_family/invites");
  assert.equal((await call(b, "DELETE", `/families/sample_family/invites/${inv.body.code}`)).status, 204);
  assert.equal((await call(b, "DELETE", `/families/sample_family/invites/${inv.body.code}`)).status, 204);
  const fam = await call(b, "GET", "/families/sample_family");
  assert.equal(fam.body.invites.find((i) => i.code === inv.body.code).revoked, true);
});

test("parity: adding a person follows api/db/people.py (400 InvalidRelation, no year range)", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const P = "/families/sample_family/people";
  const cases = [
    [{ name: "  " }, "A person needs a name"],
    [{ name: "X", born: 1990, died: 1980 }, "a person cannot die before they were born"],
    [{ name: "X", parentIds: ["rosa", "luis", "dad"] }, "a person has at most 2 parents"],
    [{ name: "X", parentIds: ["rosa", "rosa"] }, "duplicate parent id"],
    [{ name: "X", parentIds: ["ghost", "zed"] }, "unknown person id(s) in this family: ['ghost', 'zed']"],
    [{ name: "X", parentIds: ["dad"], spouseIds: ["dad"] }, "a person cannot be both a parent and a spouse"],
    [{ name: "X", parentIds: ["dad"], spouseIds: ["rosa"] }, "a person cannot be married to their own ancestor or descendant"],
    [{ name: "X", born: 1950, parentIds: ["dad"] }, "a person cannot be born before their parent (David, born 1962)"],
  ];
  for (const [body, detail] of cases) {
    const r = await call(b, "POST", P, body);
    assert.equal(r.status, 400, JSON.stringify(body));
    assert.equal(r.body.detail, detail);
  }
  const missing = await call(b, "POST", P, {});
  assert.equal(missing.status, 422);
  assert.deepEqual(missing.body.detail[0].loc, ["body", "name"]);
  const badYear = await call(b, "POST", P, { name: "Y", born: "soon" });
  assert.equal(badYear.status, 422);
  assert.equal(badYear.body.detail[0].type, "int_parsing");

  // Any year at all: the API has no range for people.
  for (const born of [1500, 2999]) assert.equal((await call(b, "POST", P, { name: `Born ${born}`, born })).status, 201);
  // A numeric string is read as a year (pydantic's lax mode); spouses are deduped.
  const ok = await call(b, "POST", P, { name: "  Inés ", born: "1912", spouseIds: ["luis", "luis"] });
  assert.equal(ok.status, 201);
  assert.equal(ok.body.name, "Inés");
  assert.equal(ok.body.born, 1912);
  assert.deepEqual(ok.body.spouseIds, ["luis"]);
  // The character sheet comes back with every field, as the API sends it.
  assert.deepEqual(Object.keys(ok.body.character).sort(), ["build", "color", "item", "itemPropId", "notes"]);
  assert.equal(ok.body.character.itemPropId, null);
  const tree = await call(b, "GET", "/families/sample_family/tree");
  for (const p of tree.body.people) assert.ok("notes" in p.character && "itemPropId" in p.character, p.id);
});

test("parity: patching a person checks the resulting tree", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const P = "/families/sample_family/people";
  const ines = (await call(b, "POST", P, { name: "Inés", born: 1910 })).body;
  // Rosa (1938) gets Inés as a parent.
  assert.equal((await call(b, "PATCH", `${P}/rosa`, { parentIds: [ines.id] })).status, 200);
  const cases = [
    [ines.id, { name: " " }, 400, "A person needs a name"],
    [ines.id, { parentIds: ["maya"] }, 400, "a person cannot be their own ancestor"],
    [ines.id, { born: 1950 }, 400, "a person cannot be born after their child (Rosa, born 1938)"],
    [ines.id, { died: 1900 }, 400, "a person cannot die before they were born"],
    [ines.id, { spouseIds: ["dad"] }, 400, "a person cannot be married to their own ancestor or descendant"],
    ["maya", { parentIds: ["dad"], spouseIds: ["dad"] }, 400, "a person cannot be both a parent and a spouse"],
    ["maya", { parentIds: ["maya"] }, 400, "a person cannot be their own parent or spouse"],
    ["maya", { born: 1950 }, 400, "a person cannot be born before their parent (David, born 1962)"],
    ["ghost", { name: "x" }, 404, "Person not found"],
  ];
  for (const [id, body, status, detail] of cases) {
    const r = await call(b, "PATCH", `${P}/${id}`, body);
    assert.equal(r.status, status, `${id} ${JSON.stringify(body)}`);
    assert.equal(r.body.detail, detail);
  }
  // A null list or name in a patch means "leave it"; null years clear them.
  const kept = await call(b, "PATCH", `${P}/${ines.id}`, { name: null, parentIds: null, born: null });
  assert.equal(kept.status, 200);
  assert.equal(kept.body.name, "Inés");
  assert.equal(kept.body.born, null);
});

test("parity: starting a family checks before writing", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const cases = [
    [{ name: " ", person: { name: "Me" } }, "A family needs a name"],
    [{ name: "F", person: { name: "Me", parentIds: ["dad"] } }, "The first person in a new family cannot have parents or spouses yet"],
    [{ name: "F", person: { name: " " } }, "A person needs a name"],
    [{ name: "F", person: { name: "Me", born: 2000, died: 1990 } }, "a person cannot die before they were born"],
  ];
  for (const [body, detail] of cases) {
    const r = await call(b, "POST", "/families", body);
    assert.equal(r.status, 400, JSON.stringify(body));
    assert.equal(r.body.detail, detail);
  }
  assert.equal((await call(b, "GET", "/me")).body.families.length, 1, "nothing was written");
  const ok = await call(b, "POST", "/families", { name: "  Side  ", person: { name: " Me " } });
  assert.equal(ok.status, 201);
  assert.equal(ok.body.family.name, "Side");
  assert.equal(ok.body.person.name, "Me");
});

test("parity: join checks the body, then the invite, then the person", async () => {
  const b = makeBackend();
  await signup(b, "Lena", "lena@example.com");
  const J = "/invites/riveras-demo/join";
  const neither = await call(b, "POST", J, {});
  assert.equal(neither.status, 422);
  assert.equal(neither.body.detail, "Send exactly one of personId (claim) or person (add yourself)");
  const both = await call(b, "POST", J, { personId: "sam", person: { name: "x" } });
  assert.equal(both.status, 422);
  assert.equal(both.body.detail, "Send exactly one of personId (claim) or person (add yourself)");
  // The body comes before the invite: a bad body on a dead link is still a 422.
  assert.equal((await call(b, "POST", "/invites/nope/join", {})).status, 422);
  const dead = await call(b, "POST", "/invites/nope/join", { personId: "sam" });
  assert.equal(dead.status, 404);
  assert.equal(dead.body.detail, "Invite not found or expired");
  const ghost = await call(b, "POST", J, { personId: "ghost" });
  assert.equal(ghost.status, 404);
  assert.equal(ghost.body.detail, "Person not found");
  const taken = await call(b, "POST", J, { personId: "dad" });
  assert.equal(taken.status, 409);
  assert.equal(taken.body.detail, "Someone has already claimed this person");
  assert.deepEqual((await call(b, "GET", "/me")).body.families, [], "a failed join leaves no membership");
  const ok = await call(b, "POST", J, { personId: "sam" });
  assert.equal(ok.status, 200);
  const other = await call(b, "POST", J, { personId: "maya" });
  assert.equal(other.status, 409);
  // The route's fixed sentence, never the db layer's (api/routes/families.py ALREADY_HAVE_PERSON).
  assert.equal(other.body.detail, "You already have a person in this family");
  const again = await call(b, "POST", J, { person: { name: "Lena again" } });
  assert.equal(again.status, 409);
  assert.equal(again.body.detail, "You already have a person in this family");
});

test("parity: size and color limits are 422s naming the limit (api/routes/families.py)", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const long = (n) => "x".repeat(n);
  const sheet = (extra) => ({ color: "#3f6fb5", ...extra });
  const P = "/families/sample_family/people";
  const cases = [
    ["POST", P, { name: long(101) }, "A name must be at most 100 characters"],
    ["POST", P, { name: "Ok", character: sheet({ color: "red" }) }, "Color must be a hex code like #3f6fb5"],
    ["POST", P, { name: "Ok", character: sheet({ notes: long(2001) }) }, "Notes must be at most 2,000 characters"],
    ["POST", P, { name: "Ok", character: sheet({ item: long(2001) }) }, "A signature item must be at most 2,000 characters"],
    ["PATCH", `${P}/sam`, { name: long(101) }, "A name must be at most 100 characters"],
    ["PATCH", `${P}/sam`, { character: sheet({ color: "#12" }) }, "Color must be a hex code like #3f6fb5"],
    // The body is cleaned before the person is looked up, as in the API.
    ["PATCH", `${P}/ghost`, { character: sheet({ color: "blue" }) }, "Color must be a hex code like #3f6fb5"],
    ["POST", "/families", { name: long(101), person: { name: "Me" } }, "A family name must be at most 100 characters"],
    ["POST", "/families", { name: "Fine", person: { name: long(101) } }, "A name must be at most 100 characters"],
  ];
  const before = (await call(b, "GET", "/families/sample_family/tree")).body.people.length;
  for (const [method, path, body, detail] of cases) {
    const r = await call(b, method, path, body);
    assert.equal(r.status, 422, `${method} ${path} ${JSON.stringify(body).slice(0, 60)}`);
    assert.equal(r.body.detail, detail);
  }
  assert.equal((await call(b, "GET", "/families/sample_family/tree")).body.people.length, before, "nothing was added");
  assert.equal((await call(b, "GET", "/me")).body.families.length, 1, "no family was started");
  // Limits count after trimming, and exactly at the limit is fine.
  const edge = await call(b, "POST", P, {
    name: `  ${long(100)}  `,
    character: sheet({ color: " #ABC ", item: "  ", notes: ` ${long(2000)} ` }),
  });
  assert.equal(edge.status, 201, JSON.stringify(edge.body).slice(0, 200));
  assert.equal(edge.body.name, long(100));
  assert.equal(edge.body.character.color, "#ABC");
  assert.equal(edge.body.character.item, null, "blank text becomes null");
  assert.equal(edge.body.character.notes, long(2000));
  // Joining by adding yourself has the same limits, after the invite is found.
  await signup(b, "Lena", "lena@example.com");
  const join = await call(b, "POST", "/invites/riveras-demo/join", { person: { name: long(101) } });
  assert.equal(join.status, 422);
  assert.equal(join.body.detail, "A name must be at most 100 characters");
  assert.deepEqual((await call(b, "GET", "/me")).body.families, [], "a failed join leaves no membership");
  const dead = await call(b, "POST", "/invites/nope/join", { person: { name: long(101) } });
  assert.equal(dead.status, 404, "the invite is checked first");
});

test("parity: questions come newest first", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const sub = await submit(b, "sample_family", { text: LONG_TEXT, year: 2004, participantIds: ["mom"] });
  await runToEnd(b, "sample_family", sub.body.runId);
  await login(b, "ana@example.com");
  const qs = (await call(b, "GET", "/families/sample_family/questions")).body.questions;
  assert.equal(qs.length, 2);
  assert.notEqual(qs[0].id, "q_mom_hymn_books", "the new question is first");
  assert.equal(qs[1].id, "q_mom_hymn_books");
});

test("parity: prop patch statuses", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const P = "/families/sample_family/props";
  const nothing = await call(b, "PATCH", `${P}/hymn_books`, {});
  assert.equal(nothing.status, 422);
  assert.equal(nothing.body.detail, "Nothing to change");
  const blank = await call(b, "PATCH", `${P}/hymn_books`, { name: "  " });
  assert.equal(blank.status, 422);
  assert.equal(blank.body.detail, "Name must not be blank");
  const ghost = await call(b, "PATCH", `${P}/ghost`, { name: "x" });
  assert.equal(ghost.status, 404);
  assert.equal(ghost.body.detail, "Not found");
  const ok = await call(b, "PATCH", `${P}/hymn_books`, { name: " Hymnals " });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.name, "Hymnals");
});

test("parity: memory submit checks the author after the fields", async () => {
  // Ana is a member whose person is unclaimed: the API's 409 NO_PERSON, after the fields are valid.
  const seed = buildDemoSeed(SAMPLE, NOW);
  seed.people.find((p) => p.id === "mom").userId = null;
  const b = createMockBackend({ seed: () => seed, stepDelayMs: 0, now: () => NOW });
  await login(b, "ana@example.com");
  const blank = await submit(b, "sample_family", { text: " ", year: 2000 });
  assert.equal(blank.status, 422);
  const noPerson = await submit(b, "sample_family", { text: "hi", year: 2000 });
  assert.equal(noPerson.status, 409);
  assert.equal(noPerson.body.detail, "Add or claim your person in this family first");
  assert.deepEqual((await call(b, "GET", "/families/sample_family/questions")).body.questions, []);
});

// ---------------------------------------------------------------------------
// Injected faults (for sweeps of error and loading states in mock mode)

test("inject: a status, a network failure, a delay, times and removal", async () => {
  const b = makeBackend();
  await login(b, "david@example.com");
  const F = "/families/sample_family";

  const off = b.inject({ method: "POST", path: "/families/{fid}/invites", status: 403, detail: "Admins only", times: 1 });
  const denied = await call(b, "POST", `${F}/invites`);
  assert.equal(denied.status, 403);
  assert.equal(denied.body.detail, "Admins only");
  assert.equal((await call(b, "POST", `${F}/invites`)).status, 201, "times: 1 is used up");
  off();

  // A 5xx with no detail is FastAPI's plain-text 500, which the client reads as its own message.
  b.inject({ path: `${F}/members/user_ana`, method: "DELETE", status: 500 });
  const client = createApiClient({ baseUrl: BASE, fetch: b.fetch, mediaUrl: b.mediaUrl });
  await assert.rejects(client.removeMember("sample_family", "user_ana"), (e) => isApiError(e) && e.status === 500);
  assert.equal((await call(b, "GET", F)).body.members.length, 2, "nothing changed");
  b.clearFaults();

  b.inject({ path: /\/tree$/, network: true });
  await assert.rejects(b.fetch(`${BASE}${F}/tree`), TypeError);
  await assert.rejects(client.getTree("sample_family"), (e) => isApiError(e) && e.status === 0);
  b.clearFaults();

  const slow = createMockBackend({ seed: () => buildDemoSeed(SAMPLE, NOW), stepDelayMs: 0, now: () => NOW });
  slow.inject({ method: "GET", path: "/me", delayMs: 30 });
  const t0 = Date.now();
  assert.equal((await slow.fetch(`${BASE}/me`)).status, 401, "a delay alone goes through");
  assert.ok(Date.now() - t0 >= 25);

  // Methods and paths that don't match go through untouched.
  b.inject({ method: "DELETE", path: F, status: 500 });
  assert.equal((await call(b, "GET", F)).status, 200);
});
