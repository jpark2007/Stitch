// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in askPage.ts (type-only imports), and
// session.ts the same, so both load directly.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  ASK_CACHE_KEY,
  ASK_CACHE_MAX,
  ASK_IDLE,
  ASK_MAX_LENGTH,
  answerText,
  askReducer,
  bestHref,
  bestMatch,
  cacheAnswer,
  cachedAnswer,
  cleanQuestion,
  enterIntent,
  fieldHint,
  groupQuotes,
  isNothing,
  memoryPageHref,
  momentTitle,
  otherGroups,
  quotesOf,
  readAskQuery,
  sameQuestion,
  tellerName,
} from "../askPage.ts";
import { hrefs } from "../session.ts";

const q = (memoryId, authorId, text, extra = {}) => ({ memoryId, authorId, text, ...extra });

const HYMN = q("sample_hymn_books", "dad", "I saw her at the top of the church steps.", {
  title: "How I met your mother",
  year: 1988,
});
const HYMN_MOM = q("sample_hymn_books", "mom", "I couldn't look up.", { title: "How I met your mother", year: 1988 });
const BIKE = q("maya_first_bike", "dad", "Maya got a red bike.", { title: "Maya's first bike", year: 1998 });

describe("the URL", () => {
  test("readAskQuery reads family and a cleaned question", () => {
    assert.deepEqual(readAskQuery("?family=f1&q=%20where%20%20did%20they%20meet%3F%20"), {
      familyId: "f1",
      q: "where did they meet?",
    });
    assert.deepEqual(readAskQuery(new URLSearchParams("q=")), { familyId: null, q: null });
    assert.deepEqual(readAskQuery(null), { familyId: null, q: null });
    assert.deepEqual(readAskQuery("family=&q=%20%20"), { familyId: null, q: null });
  });

  test("cleanQuestion collapses whitespace and caps the length", () => {
    assert.equal(cleanQuestion("  a\n\tb  "), "a b");
    assert.equal(cleanQuestion(""), null);
    assert.equal(cleanQuestion(null), null);
    assert.equal(cleanQuestion(42), null);
    assert.equal(cleanQuestion("x".repeat(ASK_MAX_LENGTH + 50)).length, ASK_MAX_LENGTH);
  });

  test("sameQuestion ignores case and spacing, never matches blanks", () => {
    assert.equal(sameQuestion("Where did they MEET?", " where  did they meet? "), true);
    assert.equal(sameQuestion("a", "b"), false);
    assert.equal(sameQuestion("", ""), false);
    assert.equal(sameQuestion(null, "a"), false);
  });

  test("hrefs.ask round-trips through readAskQuery", () => {
    assert.equal(hrefs.ask(), "/ask");
    assert.equal(hrefs.ask({ familyId: "f1" }), "/ask?family=f1");
    assert.equal(hrefs.ask({ familyId: "f1", q: "  " }), "/ask?family=f1");
    const href = hrefs.ask({ familyId: "fam/1 &2", q: " Who taught Sam? & why #1 " });
    assert.equal(href, "/ask?family=fam%2F1%20%262&q=Who%20taught%20Sam%3F%20%26%20why%20%231");
    assert.deepEqual(readAskQuery(href.split("?")[1]), { familyId: "fam/1 &2", q: "Who taught Sam? & why #1" });
  });
});

describe("links", () => {
  test("memoryPageHref opens the spread, with family and narrator", () => {
    assert.equal(
      memoryPageHref({ memoryId: "m 1", familyId: "f/1", narrator: "dad" }),
      "/memories/m%201?family=f%2F1&state=open&narrator=dad",
    );
    assert.equal(memoryPageHref({ memoryId: "m" }), "/memories/m?state=open");
  });

  test("it starts with the path and family hrefs.memory uses", () => {
    const href = memoryPageHref({ memoryId: "a b", familyId: "f1" });
    assert.ok(href.startsWith(hrefs.memory("a b", "f1")));
  });
});

describe("grouping and the best match", () => {
  test("quotesOf drops quotes with no memory or text", () => {
    const res = { answer: "", quotes: [HYMN, q("", "x", "t"), q("m", "x", "  "), null] };
    assert.deepEqual(quotesOf(res), [HYMN]);
    assert.deepEqual(quotesOf(null), []);
    assert.deepEqual(quotesOf({ answer: "x" }), []);
  });

  test("groupQuotes keeps first-seen order and fills title/year from any quote", () => {
    const groups = groupQuotes([q("a", "p1", "one"), BIKE, q("a", "p2", "two", { title: "A", year: 2001 })]);
    assert.deepEqual(
      groups.map((g) => [g.memoryId, g.title, g.year, g.quotes.length]),
      [
        ["a", "A", 2001, 2],
        ["maya_first_bike", "Maya's first bike", 1998, 1],
      ],
    );
  });

  test("bestMatch uses the server's best, with its quotes", () => {
    const res = {
      answer: "x",
      quotes: [BIKE, HYMN, HYMN_MOM],
      best: { memoryId: "sample_hymn_books", title: null, year: null, personId: "dad", confident: true },
    };
    const best = bestMatch(res);
    assert.equal(best.memoryId, "sample_hymn_books");
    assert.equal(best.title, "How I met your mother");
    assert.equal(best.year, 1988);
    assert.equal(best.confident, true);
    assert.deepEqual(best.quotes, [HYMN, HYMN_MOM]);
    assert.deepEqual(
      otherGroups(res, best).map((g) => g.memoryId),
      ["maya_first_bike"],
    );
  });

  test("best: null from a server that sets mode means nothing to open, even with quotes", () => {
    for (const mode of ["search", "model"]) {
      const res = { answer: "x", quotes: [BIKE], best: null, mode };
      assert.equal(bestMatch(res), null);
      assert.equal(isNothing(res), false);
      assert.deepEqual(otherGroups(res, null).map((g) => g.memoryId), ["maya_first_bike"]);
    }
  });

  test("best: null with no mode (the schema default before best is filled) falls back", () => {
    for (const res of [
      { answer: "x", quotes: [BIKE], best: null, mode: null },
      { answer: "x", quotes: [BIKE], best: null },
    ]) {
      const best = bestMatch(res);
      assert.equal(best.memoryId, "maya_first_bike");
      assert.equal(best.confident, false);
    }
    assert.equal(isNothing({ answer: "x", quotes: [], best: null, mode: null }), true);
  });

  test("no best field: fall back to the first quote's memory, never confident", () => {
    const best = bestMatch({ answer: "x", quotes: [HYMN, BIKE, HYMN_MOM] });
    assert.equal(best.memoryId, "sample_hymn_books");
    assert.equal(best.confident, false);
    assert.equal(best.personId, "dad");
    assert.equal(best.quotes.length, 2);
    assert.equal(bestMatch({ answer: "x", quotes: [] }), null);
    assert.equal(bestMatch(null), null);
  });

  test("a best memory with no quotes still opens, without a narrator", () => {
    const best = bestMatch({
      answer: "x",
      quotes: [],
      best: { memoryId: "m9", title: "T", year: 1970, personId: "rosa", confident: false },
    });
    assert.equal(best.personId, "rosa");
    assert.equal(bestHref(best, "f1"), "/memories/m9?family=f1&state=open");
  });

  test("bestHref starts with the quoted teller", () => {
    const best = bestMatch({ answer: "x", quotes: [HYMN_MOM, HYMN], best: { memoryId: "sample_hymn_books", confident: true } });
    assert.equal(bestHref(best, "sample_family"), "/memories/sample_hymn_books?family=sample_family&state=open&narrator=mom");
  });

  test("isNothing: no best and no quotes", () => {
    assert.equal(isNothing({ answer: "Nobody", quotes: [], best: null }), true);
    assert.equal(isNothing({ answer: "Nobody", quotes: [] }), true);
  });

  test("text helpers", () => {
    assert.equal(momentTitle(" "), "A family moment");
    assert.equal(momentTitle("Hymns"), "Hymns");
    const names = new Map([["dad", "David"]]);
    assert.equal(tellerName(names, "dad"), "David");
    assert.equal(tellerName(names, "ghost"), "Someone in the family");
    assert.equal(tellerName(null, "dad"), "Someone in the family");
    assert.equal(answerText({ answer: " Found it. ", quotes: [] }, null), "Found it.");
    assert.match(answerText({ answer: "", quotes: [] }, null), /No one/);
  });
});

describe("the state machine", () => {
  const found = { answer: "Found", quotes: [HYMN], best: { memoryId: HYMN.memoryId, confident: true } };
  const none = { answer: "Nobody", quotes: [], best: null };

  test("start → results, start → nothing", () => {
    let v = askReducer(ASK_IDLE, { type: "start", q: "meet", seq: 1 });
    assert.equal(v.kind, "searching");
    assert.equal(v.shown, null);
    v = askReducer(v, { type: "done", seq: 1, res: found });
    assert.equal(v.kind, "results");
    v = askReducer(v, { type: "start", q: "moon", seq: 2 });
    assert.equal(v.kind, "searching");
    assert.equal(v.shown.kind, "results", "the last answer stays up while the next one loads");
    v = askReducer(v, { type: "done", seq: 2, res: none });
    assert.equal(v.kind, "nothing");
    assert.equal(v.q, "moon");
  });

  test("a slower, earlier search never overwrites a newer one", () => {
    let v = askReducer(ASK_IDLE, { type: "start", q: "first", seq: 1 });
    v = askReducer(v, { type: "start", q: "second", seq: 2 });
    const late = askReducer(v, { type: "done", seq: 1, res: found });
    assert.equal(late, v);
    const lateFail = askReducer(v, { type: "failed", seq: 1, message: "x" });
    assert.equal(lateFail, v);
    v = askReducer(v, { type: "done", seq: 2, res: none });
    assert.equal(v.q, "second");
    assert.equal(askReducer(v, { type: "done", seq: 2, res: found }), v, "nothing lands after the answer");
  });

  test("failure keeps the question; clear goes idle", () => {
    let v = askReducer(ASK_IDLE, { type: "start", q: "meet", seq: 3 });
    v = askReducer(v, { type: "failed", seq: 3, message: "Offline" });
    assert.deepEqual(v, { kind: "error", q: "meet", seq: 3, message: "Offline" });
    assert.equal(askReducer(v, { type: "clear" }), ASK_IDLE);
  });

  test("enterIntent", () => {
    assert.deepEqual(enterIntent(ASK_IDLE, "  ", "f"), { kind: "none" });
    assert.deepEqual(enterIntent(ASK_IDLE, " meet ", "f"), { kind: "ask", q: "meet" });
    const results = { kind: "results", q: "meet", seq: 1, res: found };
    assert.deepEqual(enterIntent(results, "Meet", "f"), {
      kind: "open",
      href: "/memories/sample_hymn_books?family=f&state=open&narrator=dad",
    });
    assert.deepEqual(enterIntent(results, "other", "f"), { kind: "ask", q: "other" });
    const unsure = { ...results, res: { ...found, best: { ...found.best, confident: false } } };
    assert.deepEqual(enterIntent(unsure, "meet", "f"), { kind: "stay" });
    const error = { kind: "error", q: "meet", seq: 1, message: "x" };
    assert.deepEqual(enterIntent(error, "meet", "f"), { kind: "retry", q: "meet" });
    const searching = { kind: "searching", q: "meet", seq: 1, shown: null };
    assert.deepEqual(enterIntent(searching, "meet", "f"), { kind: "stay" });
    assert.deepEqual(enterIntent({ kind: "nothing", q: "meet", seq: 1, res: none }, "meet", "f"), { kind: "stay" });
  });

  test("fieldHint says what Enter does next", () => {
    const results = { kind: "results", q: "meet", seq: 1, res: found };
    assert.match(fieldHint(results, "meet"), /Press Enter again/);
    assert.doesNotMatch(fieldHint(results, "meet them"), /Press Enter/);
    const unsure = { ...results, res: { ...found, best: { ...found.best, confident: false } } };
    assert.doesNotMatch(fieldHint(unsure, "meet"), /Press Enter/);
    assert.match(fieldHint({ kind: "searching", q: "meet", seq: 2, shown: null }, "meet"), /Looking/);
    assert.match(fieldHint({ kind: "error", q: "meet", seq: 3, message: "x" }, "meet"), /try again/);
    assert.ok(fieldHint(ASK_IDLE, "").length > 0, "never empty, so the card keeps its height");
  });
});

describe("the per-tab cache", () => {
  function memoryStore() {
    const data = new Map();
    return {
      data,
      getItem: (k) => (data.has(k) ? data.get(k) : null),
      setItem: (k, v) => data.set(k, String(v)),
    };
  }
  const res = { answer: "Found", quotes: [HYMN] };

  test("keeps an answer per family and question", () => {
    const s = memoryStore();
    assert.equal(cachedAnswer(s, "f1", "meet"), null);
    cacheAnswer(s, "f1", "meet", res);
    assert.deepEqual(cachedAnswer(s, "f1", " MEET "), res);
    assert.equal(cachedAnswer(s, "f2", "meet"), null);
  });

  test("newest first, capped", () => {
    const s = memoryStore();
    for (let i = 0; i < ASK_CACHE_MAX + 3; i++) cacheAnswer(s, "f", `q${i}`, res);
    const list = JSON.parse(s.data.get(ASK_CACHE_KEY));
    assert.equal(list.length, ASK_CACHE_MAX);
    assert.equal(list[0].q, `q${ASK_CACHE_MAX + 2}`);
    assert.equal(cachedAnswer(s, "f", "q0"), null);
  });

  test("never throws on missing, broken or failing storage", () => {
    assert.equal(cachedAnswer(null, "f", "q"), null);
    cacheAnswer(null, "f", "q", res);
    const broken = { getItem: () => "{not json", setItem: () => {} };
    assert.equal(cachedAnswer(broken, "f", "q"), null);
    const throwing = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("full");
      },
    };
    assert.equal(cachedAnswer(throwing, "f", "q"), null);
    cacheAnswer(throwing, "f", "q", res);
    const odd = { getItem: () => JSON.stringify([{ familyId: "f", q: "q", res: { answer: "x" } }, 7]), setItem() {} };
    assert.equal(cachedAnswer(odd, "f", "q"), null);
  });
});
