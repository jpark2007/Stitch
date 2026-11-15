// Run with: cd web && node --test --experimental-strip-types "components/demo/__tests__/*.test.mjs"
import { describe, test } from "node:test";
import assert from "node:assert/strict";

import {
  DRAFT_KEY,
  STORIES_KEY,
  MAX_STORIES,
  draftJson,
  exportStories,
  importStories,
  inputFromStory,
  loadStories,
  newStoryId,
  parseStory,
  saveStories,
  storiesReducer,
  storyErrors,
  storyFromInput,
  writeDraft,
} from "../stories.ts";
import { DEMO_DRAFT_KEY, demoDraftCarry } from "../../flow/addPage.ts";

const fair = { id: "s1", title: "The county fair", year: 1968, text: "Grace and I met at the county fair.", question: "How did you and Grace meet?" };
const tamales = { id: "s2", title: "Sunday tamales", year: 1975, text: "Every Sunday we made tamales.", question: "" };

function memoryStorage() {
  const map = new Map();
  return { map, getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => void map.set(k, String(v)) };
}
const blocked = {
  getItem() {
    throw new Error("SecurityError");
  },
  setItem() {
    throw new Error("QuotaExceededError");
  },
};

describe("storiesReducer", () => {
  test("add, update, remove", () => {
    let s = storiesReducer([], { type: "add", story: fair });
    s = storiesReducer(s, { type: "add", story: tamales });
    assert.deepEqual(s.map((x) => x.id), ["s1", "s2"]);
    s = storiesReducer(s, { type: "update", story: { ...fair, title: "The fair" } });
    assert.equal(s[0].title, "The fair");
    s = storiesReducer(s, { type: "remove", id: "s1" });
    assert.deepEqual(s.map((x) => x.id), ["s2"]);
  });

  test("adding an id already there changes nothing; the state is never mutated", () => {
    const start = Object.freeze([fair]);
    const s = storiesReducer(start, { type: "add", story: { ...fair, title: "Other" } });
    assert.deepEqual(s, [fair]);
    assert.notEqual(s, start);
  });

  test("import skips tellings already in the library (same words) and renames clashing ids", () => {
    const s = storiesReducer([fair], {
      type: "import",
      stories: [
        { ...fair, id: "other", text: "  Grace and I   met at the county fair. " },
        { ...tamales, id: "s1" },
        { ...tamales, id: "s9" },
      ],
    });
    assert.deepEqual(s.map((x) => x.id), ["s1", "s1-2"]);
    assert.equal(s[1].text, tamales.text);
  });

  test("replace swaps the whole library; the library is capped", () => {
    assert.deepEqual(storiesReducer([fair], { type: "replace", stories: [tamales] }), [tamales]);
    const many = Array.from({ length: MAX_STORIES + 5 }, (_, i) => ({ ...fair, id: `x${i}`, text: `story ${i}` }));
    assert.equal(storiesReducer([], { type: "import", stories: many }).length, MAX_STORIES);
  });
});

describe("storyErrors / storyFromInput", () => {
  test("a valid story", () => {
    const input = { title: " The fair ", year: "1968", text: "We met.\n", question: " How? " };
    assert.deepEqual(storyErrors(input, 2026), {});
    assert.deepEqual(storyFromInput(input, "id1"), { id: "id1", title: "The fair", year: 1968, text: "We met.\n", question: "How?" });
  });

  test("the telling is kept exactly (replay matches it word for word)", () => {
    assert.equal(storyFromInput({ title: "t", year: "", text: "  Exactly  this. ", question: "" }, "i").text, "  Exactly  this. ");
  });

  test("title and text are required; the year is optional but bounded", () => {
    const e = storyErrors({ title: " ", year: "1700", text: "   ", question: "" }, 2026);
    assert.ok(e.title && e.text && e.year);
    assert.equal(storyErrors({ title: "t", year: "", text: "x", question: "" }, 2026).year, undefined);
    assert.ok(storyErrors({ title: "t", year: "2027", text: "x", question: "" }, 2026).year);
    assert.ok(storyErrors({ title: "t", year: "68", text: "x", question: "" }, 2026).year);
    assert.equal(storyFromInput({ title: "t", year: "", text: "x", question: "" }, "i").year, null);
  });

  test("too long fields", () => {
    assert.ok(storyErrors({ title: "x".repeat(201), year: "", text: "x", question: "" }).title);
    assert.ok(storyErrors({ title: "t", year: "", text: "x", question: "q".repeat(301) }).question);
  });

  test("inputFromStory round-trips", () => {
    assert.deepEqual(inputFromStory(fair), { title: fair.title, year: "1968", text: fair.text, question: fair.question });
    assert.deepEqual(inputFromStory(null), { title: "", year: "", text: "", question: "" });
  });

  test("new ids are distinct and safe", () => {
    const a = newStoryId(1, () => 0.1);
    const b = newStoryId(1, () => 0.2);
    assert.notEqual(a, b);
    assert.match(a, /^story-[\w-]+$/);
  });
});

describe("importStories / exportStories", () => {
  test("export then import gives the same stories", () => {
    const r = importStories(exportStories([fair, tamales]), 2026);
    assert.deepEqual(r, { ok: true, stories: [fair, tamales], skipped: 0 });
  });

  test("a bare list or a single story is accepted", () => {
    assert.equal(importStories(JSON.stringify([fair]), 2026).stories.length, 1);
    assert.equal(importStories(JSON.stringify(fair), 2026).stories.length, 1);
  });

  test("entries without a telling are skipped and counted", () => {
    const r = importStories(JSON.stringify({ stories: [fair, { title: "no text" }, 4, null] }), 2026);
    assert.equal(r.ok, true);
    assert.equal(r.stories.length, 1);
    assert.equal(r.skipped, 3);
  });

  test("errors: empty, invalid JSON, wrong shape, nothing usable", () => {
    for (const bad of ["", "  ", "{", "42", '"x"', "{}", JSON.stringify({ stories: [{ title: "x" }] })]) {
      const r = importStories(bad, 2026);
      assert.equal(r.ok, false, bad);
      assert.equal(typeof r.error, "string");
    }
  });

  test("parseStory fills what is missing and drops what is wrong", () => {
    const s = parseStory({ text: "We drove all night to the coast and back" }, "fb", 2026);
    assert.equal(s.id, "fb");
    assert.equal(s.title, "We drove all night to the");
    assert.equal(s.year, null);
    assert.equal(s.question, "");
    assert.equal(parseStory({ text: "x", year: "1990", id: "ok-id" }, "fb", 2026).year, 1990);
    assert.equal(parseStory({ text: "x", year: 3000 }, "fb", 2026).year, null);
    assert.equal(parseStory({ text: "x", id: "<script>" }, "fb", 2026).id, "fb");
  });
});

describe("storage", () => {
  test("save then load; nothing, junk or blocked storage loads as empty", () => {
    const s = memoryStorage();
    assert.equal(saveStories(s, [fair]), true);
    assert.ok(s.map.has(STORIES_KEY));
    assert.deepEqual(loadStories(s), [fair]);
    assert.deepEqual(loadStories(memoryStorage()), []);
    const junk = memoryStorage();
    junk.setItem(STORIES_KEY, "{nope");
    assert.deepEqual(loadStories(junk), []);
    assert.deepEqual(loadStories(blocked), []);
    assert.deepEqual(loadStories(null), []);
    assert.equal(saveStories(blocked, [fair]), false);
    assert.equal(saveStories(null, [fair]), false);
  });

  test("the draft is what the add page reads with ?demoDraft=1", () => {
    assert.equal(DRAFT_KEY, DEMO_DRAFT_KEY);
    const s = memoryStorage();
    assert.equal(writeDraft(s, fair), true);
    assert.deepEqual(JSON.parse(s.map.get(DRAFT_KEY)), {
      text: fair.text,
      year: "1968",
      title: fair.title,
      question: fair.question,
    });
    const carry = demoDraftCarry(new URLSearchParams("demoDraft=1"), s, "demo_family");
    assert.deepEqual(carry, { familyId: "demo_family", text: fair.text, year: "1968", question: fair.question });
    assert.equal(JSON.parse(draftJson({ ...tamales, year: null })).year, "");
    assert.equal(writeDraft(blocked, fair), false);
  });
});
