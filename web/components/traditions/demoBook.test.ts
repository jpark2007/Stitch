/**
 * The demo Book of Family Traditions is the demo tree's family: every
 * participant is one of its people, and the cover carries its name.
 *
 *   cd web && node --no-warnings --test "components/traditions/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

// By computed paths, so the type check reads them as the app does (no ".ts").
const paths = ["./demoBook.ts", "./traditionBook.ts", "../tree/demoFamily.ts", "../flow/mock/demoTraditions.ts"];
const { demoTraditionsBook, DEMO_PERSON_IDS }: typeof import("./demoBook") = await import(paths[0]);
const { traditionBook, familyCore }: typeof import("./traditionBook") = await import(paths[1]);
const { demoFamily, demoPeople }: typeof import("../tree/demoFamily") = await import(paths[2]);
const { DEMO_TRADITIONS }: typeof import("../flow/mock/demoTraditions") = await import(paths[3]);

describe("the demo book", () => {
  const input = demoTraditionsBook(DEMO_TRADITIONS, demoFamily, demoPeople, new Date("2026-11-14T00:00:00Z"));
  const treeIds = new Set(demoPeople.map((p) => p.id));

  it("keeps all five traditions, on the demo tree's family", () => {
    assert.equal(input.traditions.length, DEMO_TRADITIONS.length);
    assert.equal(input.familyName, demoFamily.name);
    for (const t of input.traditions) assert.equal(t.familyId, demoFamily.id);
  });

  it("names only people on the demo tree, and loses none of the seed's keepers", () => {
    for (const [i, t] of input.traditions.entries()) {
      assert.ok(t.participantIds.length > 0, t.id);
      for (const id of t.participantIds) assert.ok(treeIds.has(id), `${t.id}: ${id} is not on the demo tree`);
      assert.equal(t.participantIds.length, new Set(DEMO_TRADITIONS[i].participantIds).size, t.id);
    }
    for (const id of Object.values(DEMO_PERSON_IDS)) assert.ok(treeIds.has(id), id);
  });

  it("tells no story about a seed-only person", () => {
    for (const t of input.traditions) assert.doesNotMatch(t.description ?? "", /\b(Maya|Sam)\b/, t.id);
  });

  it("binds a book whose every keeper is a demo tree person and whose cover uses the family's name", () => {
    const book = traditionBook(input.traditions, input.people, { familyName: input.familyName });
    assert.equal(book.cover.kicker, `The ${familyCore(demoFamily.name)}`);
    assert.equal(book.title, "The Alvarez–Moore Book of Traditions");
    assert.equal(book.byId.size, 5);
    for (const { keepers } of book.byId.values()) {
      assert.ok(keepers.length > 0);
      for (const k of keepers) assert.ok(treeIds.has(k.id), k.id);
    }
    const end = book.spreads[book.spreads.length - 1];
    for (const t of end.tellings ?? []) assert.ok(treeIds.has(t.id), t.id);
  });
});
