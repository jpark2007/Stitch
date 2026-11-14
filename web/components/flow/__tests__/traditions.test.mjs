// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// The Book of Family Traditions in mock mode: the demo seed and the five
// routes, through the real API client.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { buildDemoSeed } from "../mock/demoSeed.ts";
import { buildDemoTraditions, DEMO_TRADITIONS } from "../mock/demoTraditions.ts";
import {
  createMockBackend,
  MAX_TRADITION_DESCRIPTION_CHARS,
  MAX_TRADITION_PHOTOS,
  MAX_TRADITION_TITLE_CHARS,
  MOCK_STORAGE_KEY,
} from "../mock/mockBackend.ts";
import { createApiClient } from "../../../lib/api.ts";

const SAMPLE = JSON.parse(
  fs.readFileSync(new URL("../../../public/samples/hymn_books.memory.json", import.meta.url), "utf8"),
);
const NOW = new Date("2026-11-13T12:00:00Z");
const FID = "sample_family";

function seed() {
  const s = buildDemoSeed(SAMPLE, NOW);
  return { ...s, traditions: buildDemoTraditions(s.families[0].id, NOW) };
}

function setup(opts = {}) {
  const backend = createMockBackend({ seed: opts.seed ?? seed, stepDelayMs: 0, now: () => NOW, storage: opts.storage });
  const api = createApiClient({ baseUrl: "http://localhost:8000", fetch: backend.fetch });
  return { backend, api };
}

async function rejects(promise, status) {
  try {
    await promise;
  } catch (e) {
    assert.equal(e.status, status, e.detail);
    return e;
  }
  assert.fail(`expected a ${status}`);
}

test("demoTraditions.ts has only type imports, and index.ts adds it to the seed", () => {
  const src = fs.readFileSync(new URL("../mock/demoTraditions.ts", import.meta.url), "utf8");
  for (const line of src.split("\n").filter((l) => /^\s*import\s/.test(l))) assert.match(line, /^import type /);
  const index = fs.readFileSync(new URL("../mock/index.ts", import.meta.url), "utf8");
  assert.match(index, /buildDemoTraditions\(/);
});

test("the five demo traditions use the demo family's people and no photos", () => {
  const people = new Set(buildDemoSeed(SAMPLE, NOW).people.map((p) => p.id));
  assert.equal(DEMO_TRADITIONS.length, 5);
  assert.equal(new Set(DEMO_TRADITIONS.map((t) => t.id)).size, 5);
  for (const t of DEMO_TRADITIONS) {
    assert.ok(t.participantIds.length > 0, t.id);
    for (const id of t.participantIds) assert.ok(people.has(id), `${t.id}: ${id}`);
    assert.deepEqual(t.photoIds, []);
    assert.ok(t.title.length <= MAX_TRADITION_TITLE_CHARS);
    if (t.when.month != null) assert.ok(t.when.month >= 1 && t.when.month <= 12);
  }
  const built = buildDemoTraditions("fam_x", NOW);
  assert.ok(built.every((t) => t.familyId === "fam_x" && t.createdAt === NOW.toISOString()));
});

test("CRUD round trip for a member, scoped to the family", async () => {
  const { api } = setup();
  await rejects(api.listTraditions(FID), 401);
  await api.login({ email: "ana@example.com", password: "stitch-demo" });

  const listed = await api.listTraditions(FID);
  assert.equal(listed.traditions.length, 5);

  const created = await api.createTradition(FID, {
    title: "  Card night  ",
    category: "game",
    when: { kind: "monthly", label: "first Friday" },
    participantIds: ["dad", "sam"],
  });
  assert.equal(created.title, "Card night");
  assert.equal(created.description, "");
  assert.deepEqual(created.photoIds, []);
  assert.equal(created.startedYear, null);
  assert.equal(created.createdBy, "user_ana");
  assert.deepEqual(await api.getTradition(FID, created.id), created);

  const patched = await api.updateTradition(FID, created.id, { startedYear: 2001, when: { kind: "weekly" } });
  assert.equal(patched.title, "Card night", "fields not sent are unchanged");
  assert.equal(patched.startedYear, 2001);
  assert.deepEqual(patched.when, { kind: "weekly" });
  const cleared = await api.updateTradition(FID, created.id, { startedYear: null, title: null });
  assert.equal(cleared.startedYear, null, "startedYear: null clears it");
  assert.equal(cleared.title, "Card night", "title: null leaves it");

  await api.deleteTradition(FID, created.id);
  await rejects(api.getTradition(FID, created.id), 404);
  assert.equal((await api.listTraditions(FID)).traditions.length, 5);

  // Another family: its own empty book, and none of the Riveras' traditions.
  const fam = await api.createFamily({ name: "The Maos", person: { name: "Ana Mao" } });
  const other = fam.family.id;
  assert.deepEqual((await api.listTraditions(other)).traditions, []);
  const tamales = DEMO_TRADITIONS[0].id;
  await rejects(api.getTradition(other, tamales), 404);
  await rejects(api.updateTradition(other, tamales, { title: "Mine now" }), 404);
  await rejects(api.deleteTradition(other, tamales), 404);
  assert.equal((await api.getTradition(FID, tamales)).title, DEMO_TRADITIONS[0].title);
});

test("a non-member gets 404 for the family's traditions", async () => {
  const { api } = setup();
  await api.signup({ email: "x@example.com", password: "correct-horse", name: "X" });
  await rejects(api.listTraditions(FID), 404);
  await rejects(api.createTradition(FID, { title: "Mine", category: "food", when: { kind: "yearly" } }), 404);
});

test("validation: bounds and enums are 422s with field errors", async () => {
  const { api } = setup();
  await api.login({ email: "david@example.com", password: "stitch-demo" });
  const ok = { title: "Kite day", category: "other", when: { kind: "yearly", month: 3 } };

  const cases = [
    [{ ...ok, title: "   " }, "title"],
    [{ ...ok, title: "x".repeat(MAX_TRADITION_TITLE_CHARS + 1) }, "title"],
    [{ ...ok, description: "x".repeat(MAX_TRADITION_DESCRIPTION_CHARS + 1) }, "description"],
    [{ ...ok, category: "sport" }, "category"],
    [{ ...ok, when: { kind: "daily" } }, "kind"],
    [{ ...ok, when: { kind: "yearly", month: 13 } }, "month"],
    [{ ...ok, when: { kind: "yearly", month: 0 } }, "month"],
    [{ ...ok, photoIds: Array.from({ length: MAX_TRADITION_PHOTOS + 1 }, (_, i) => `p${i}`) }, "photoIds"],
    [{ ...ok, startedYear: 1799 }, "startedYear"],
    [{ ...ok, startedYear: 2027 }, "startedYear"],
    [{ category: "food", when: { kind: "weekly" } }, "title"],
    [{ ...ok, color: "red" }, "color"],
  ];
  for (const [body, field] of cases) {
    const e = await rejects(api.createTradition(FID, body), 422);
    assert.ok(field in e.fieldErrors, `${JSON.stringify(body).slice(0, 80)} -> ${JSON.stringify(e.fieldErrors)}`);
  }
  const tamales = DEMO_TRADITIONS[0].id;
  const e = await rejects(api.updateTradition(FID, tamales, { category: "sport" }), 422);
  assert.match(e.fieldErrors.category, /'food', .* or 'other'/);
  // Nothing was written by the failed calls.
  assert.equal((await api.listTraditions(FID)).traditions.length, 5);
});

test("a stored state from before traditions existed gets the seed's", async () => {
  const data = new Map();
  const storage = {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  };
  const first = setup({ storage });
  await first.api.login({ email: "ana@example.com", password: "stitch-demo" });
  const stored = JSON.parse(data.get(MOCK_STORAGE_KEY));
  delete stored.traditions;
  data.set(MOCK_STORAGE_KEY, JSON.stringify(stored));

  const second = setup({ storage });
  assert.equal((await second.api.listTraditions(FID)).traditions.length, 5);
});
