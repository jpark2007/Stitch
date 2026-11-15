// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in addPage.ts; it has type-only imports,
// so no resolve hook is needed.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  CARRY_KEY,
  DRAFT_KEY,
  DRAFT_KEY_PREFIX,
  OPEN_RUNS_KEY,
  OPEN_RUNS_MAX,
  RUN_GONE_MESSAGE,
  TELLING_KEY_PREFIX,
  addRelativeHref,
  addedFromQuery,
  carriedPhotosNote,
  carryInto,
  clearCarry,
  clearTypedDraft,
  draftForForm,
  preselectAdded,
  questionIdFromQuery,
  readTypedDraft,
  saveTypedDraft,
  withQuestionId,
  withoutAdded,
  draftFromSent,
  familyChoice,
  hasTold,
  loadSentDraft,
  loadTelling,
  momentFromMemory,
  momentFromSummary,
  momentsFromThreads,
  questionForMemory,
  readAddQuery,
  readCarry,
  restoreNotice,
  retryHref,
  runHref,
  saveCarry,
  saveSentDraft,
  saveTelling,
  sentDraftFromSubmit,
  tellingFromSubmit,
  withRunGone,
  familyAsking,
  findMomentFamily,
  firstWords,
  forgetOpenRun,
  markRunWaiting,
  openRunNotice,
  openRunsFor,
  rememberOpenRun,
  runToResume,
  DEMO_DRAFT_KEY,
  demoDraftCarry,
  dropDemoDraft,
} from "../addPage.ts";
import { pickFamily, safeNext } from "../session.ts";

/** A Map-backed Storage stand-in. */
function memoryStorage() {
  const map = new Map();
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => void map.set(k, String(v)),
  };
}

const throwing = {
  getItem() {
    throw new Error("SecurityError: storage is blocked");
  },
  setItem() {
    throw new Error("QuotaExceededError");
  },
};

// ---- readAddQuery -------------------------------------------------------------

describe("readAddQuery", () => {
  test("reads every param", () => {
    assert.deepEqual(readAddQuery("?family=fam_1&memoryId=m_2&run=run_3"), {
      familyId: "fam_1",
      memoryId: "m_2",
      runId: "run_3",
      retryOf: null,
    });
  });

  test("works without the leading ?", () => {
    assert.deepEqual(readAddQuery("family=f&run=r"), { familyId: "f", memoryId: null, runId: "r", retryOf: null });
  });

  test("missing params are null", () => {
    assert.deepEqual(readAddQuery(""), { familyId: null, memoryId: null, runId: null, retryOf: null });
    assert.deepEqual(readAddQuery("?other=1"), { familyId: null, memoryId: null, runId: null, retryOf: null });
    assert.deepEqual(readAddQuery(null), { familyId: null, memoryId: null, runId: null, retryOf: null });
  });

  test("empty values are null", () => {
    assert.deepEqual(readAddQuery("?family=&memoryId=&run="), {
      familyId: null,
      memoryId: null,
      runId: null,
      retryOf: null,
    });
    assert.deepEqual(readAddQuery("?family&run"), { familyId: null, memoryId: null, runId: null, retryOf: null });
  });

  test("takes a URLSearchParams and decodes values", () => {
    const params = new URLSearchParams();
    params.set("family", "fam a/b");
    params.set("memoryId", "m&1");
    assert.deepEqual(readAddQuery(params), { familyId: "fam a/b", memoryId: "m&1", runId: null, retryOf: null });
  });

  test("the first of repeated params wins", () => {
    assert.equal(readAddQuery("?run=a&run=b").runId, "a");
  });

  test("reads ?retry= into retryOf; missing or empty is null", () => {
    assert.equal(readAddQuery("?family=f&retry=run_9").retryOf, "run_9");
    assert.equal(readAddQuery(new URLSearchParams("retry=r%2F1")).retryOf, "r/1");
    assert.equal(readAddQuery("?family=f").retryOf, null);
    assert.equal(readAddQuery("?retry=").retryOf, null);
    assert.equal(readAddQuery("?retry").retryOf, null);
    assert.equal(readAddQuery(null).retryOf, null);
  });
});

// ---- familyChoice ---------------------------------------------------------------

describe("familyChoice", () => {
  const lees = { id: "fam_lees", name: "The Lees", role: "admin", personId: "p_1" };
  const parks = { id: "fam_parks", name: "The Parks", role: "member", personId: "p_2" };
  const kims = { id: "fam_kims", name: "The Kims", role: "member", personId: null };

  test("no families: nothing current, nothing to show", () => {
    assert.deepEqual(familyChoice([], null), { current: null, others: [], show: false });
    assert.deepEqual(familyChoice([], "fam_lees"), { current: null, others: [], show: false });
  });

  test("one family is current and not shown", () => {
    assert.deepEqual(familyChoice([lees], "fam_lees"), { current: lees, others: [], show: false });
    assert.deepEqual(familyChoice([lees], null), { current: lees, others: [], show: false });
    assert.deepEqual(familyChoice([lees], "fam_other"), { current: lees, others: [], show: false });
  });

  test("two families with the current one set", () => {
    assert.deepEqual(familyChoice([lees, parks], "fam_parks"), {
      current: parks,
      others: [lees],
      show: true,
    });
    assert.deepEqual(familyChoice([lees, parks], "fam_lees"), {
      current: lees,
      others: [parks],
      show: true,
    });
  });

  test("a missing or unknown id falls back the way pickFamily does", () => {
    const families = [parks, lees, kims];
    for (const id of [null, undefined, "", "fam_gone"]) {
      const choice = familyChoice(families, id);
      assert.equal(choice.current, pickFamily(families, id), String(id));
      assert.equal(choice.current, parks);
      assert.deepEqual(choice.others, [lees, kims]);
      assert.equal(choice.show, true);
    }
    for (const f of families) {
      assert.equal(familyChoice(families, f.id).current, pickFamily(families, f.id));
    }
  });

  test("the others keep the session's order", () => {
    assert.deepEqual(familyChoice([lees, parks, kims], "fam_parks").others, [lees, kims]);
    assert.deepEqual(familyChoice([kims, lees, parks], "fam_kims").others, [lees, parks]);
    assert.deepEqual(familyChoice([kims, lees, parks], "fam_parks").others, [kims, lees]);
  });

  test("does not change the list it is given", () => {
    const families = [lees, parks];
    const choice = familyChoice(families, "fam_lees");
    choice.others.push(kims);
    assert.deepEqual(families, [lees, parks]);
  });
});

// ---- runHref ------------------------------------------------------------------

describe("runHref", () => {
  test("plain ids", () => {
    assert.equal(runHref({ familyId: "fam_1", runId: "run_9" }), "/add?family=fam_1&run=run_9");
  });

  test("URL-encodes both ids and round-trips through readAddQuery", () => {
    const href = runHref({ familyId: "a&b=c", runId: "r/1 ?#" });
    assert.equal(href, "/add?family=a%26b%3Dc&run=r%2F1%20%3F%23");
    const q = readAddQuery(href.slice(href.indexOf("?")));
    assert.deepEqual(q, { familyId: "a&b=c", memoryId: null, runId: "r/1 ?#", retryOf: null });
  });
});

// ---- retryHref ------------------------------------------------------------------

describe("retryHref", () => {
  test("plain ids", () => {
    assert.equal(retryHref({ familyId: "fam_1", runId: "run_9" }), "/add?family=fam_1&retry=run_9");
  });

  test("URL-encodes both ids and round-trips through readAddQuery", () => {
    const href = retryHref({ familyId: "a&b=c", runId: "r/1 ?#" });
    assert.equal(href, "/add?family=a%26b%3Dc&retry=r%2F1%20%3F%23");
    const q = readAddQuery(href.slice(href.indexOf("?")));
    assert.deepEqual(q, { familyId: "a&b=c", memoryId: null, runId: null, retryOf: "r/1 ?#" });
  });
});

// ---- tellingFromSubmit ----------------------------------------------------------

describe("tellingFromSubmit", () => {
  test("with a question", () => {
    const t = tellingFromSubmit(
      { text: "We met at the dance hall.", year: 1987, question: "  How did you two meet? ", participantIds: ["a"] },
      2,
    );
    assert.deepEqual(t, {
      question: "How did you two meet?",
      text: "We met at the dance hall.",
      year: 1987,
      photoCount: 2,
      secondTellingOf: null,
    });
  });

  test("without a question", () => {
    assert.deepEqual(tellingFromSubmit({ text: "A story.", year: 2001 }, 0), {
      question: null,
      text: "A story.",
      year: 2001,
      photoCount: 0,
      secondTellingOf: null,
    });
    assert.equal(tellingFromSubmit({ text: "x", year: 2001, question: "   " }, 0).question, null);
    assert.equal(tellingFromSubmit({ text: "x", year: 2001, question: null }, 0).question, null);
  });

  test("a second telling carries the moment's title", () => {
    const t = tellingFromSubmit(
      { text: "I remember the hymn books.", year: 1988, memoryId: "hymn_books", question: "What do you remember?" },
      1,
      "The hymn books",
    );
    assert.equal(t.secondTellingOf, "The hymn books");
    assert.equal(t.question, "What do you remember?");
    assert.equal(t.year, 1988);
    assert.equal(t.photoCount, 1);
    assert.equal(tellingFromSubmit({ text: "x", year: 1 }, 0, "  ").secondTellingOf, null);
    assert.equal(tellingFromSubmit({ text: "x", year: 1 }, 0, null).secondTellingOf, null);
  });

  test("a nonsense photo count becomes 0", () => {
    assert.equal(tellingFromSubmit({ text: "x", year: 1990 }, -3).photoCount, 0);
    assert.equal(tellingFromSubmit({ text: "x", year: 1990 }, Number.NaN).photoCount, 0);
  });
});

// ---- saveTelling / loadTelling ------------------------------------------------------

const TELLING = {
  question: "How did you two meet?",
  text: "We met at the dance hall.\nIt was raining.",
  year: 1987,
  photoCount: 1,
  secondTellingOf: null,
};

describe("saveTelling and loadTelling", () => {
  test("round trip, under the run's own key", () => {
    const s = memoryStorage();
    saveTelling(s, "run_1", TELLING);
    assert.ok(s.map.has(`${TELLING_KEY_PREFIX}run_1`));
    assert.equal(TELLING_KEY_PREFIX, "stitch.telling.");
    assert.deepEqual(loadTelling(s, "run_1"), TELLING);
    assert.equal(loadTelling(s, "run_2"), null);
  });

  test("round trip of a second telling", () => {
    const s = memoryStorage();
    const t = { ...TELLING, question: null, secondTellingOf: "The hymn books" };
    saveTelling(s, "run_x", t);
    assert.deepEqual(loadTelling(s, "run_x"), t);
  });

  test("a saved slip without the optional fields loads with nulls", () => {
    const s = memoryStorage();
    s.setItem(`${TELLING_KEY_PREFIX}r`, JSON.stringify({ text: "t", year: 2000, photoCount: 0 }));
    assert.deepEqual(loadTelling(s, "r"), {
      question: null,
      text: "t",
      year: 2000,
      photoCount: 0,
      secondTellingOf: null,
    });
  });

  test("null storage and an empty run id do nothing", () => {
    assert.doesNotThrow(() => saveTelling(null, "run_1", TELLING));
    assert.equal(loadTelling(null, "run_1"), null);
    const s = memoryStorage();
    saveTelling(s, "", TELLING);
    assert.equal(s.map.size, 0);
    assert.equal(loadTelling(s, ""), null);
  });

  test("missing key", () => {
    assert.equal(loadTelling(memoryStorage(), "nope"), null);
  });

  test("invalid JSON", () => {
    const s = memoryStorage();
    s.setItem(`${TELLING_KEY_PREFIX}r`, "{not json");
    assert.equal(loadTelling(s, "r"), null);
  });

  test("wrong shapes", () => {
    const bad = [
      "null",
      "42",
      '"a string"',
      "[]",
      JSON.stringify({ ...TELLING, text: 5 }),
      JSON.stringify({ ...TELLING, year: "1987" }),
      JSON.stringify({ ...TELLING, year: null }),
      JSON.stringify({ ...TELLING, photoCount: -1 }),
      JSON.stringify({ ...TELLING, photoCount: "1" }),
      JSON.stringify({ ...TELLING, question: 3 }),
      JSON.stringify({ ...TELLING, secondTellingOf: { title: "x" } }),
      JSON.stringify({ year: 1987, photoCount: 0 }),
    ];
    for (const raw of bad) {
      const s = memoryStorage();
      s.setItem(`${TELLING_KEY_PREFIX}r`, raw);
      assert.equal(loadTelling(s, "r"), null, raw);
    }
  });

  test("a throwing storage never throws", () => {
    assert.doesNotThrow(() => saveTelling(throwing, "run_1", TELLING));
    assert.equal(loadTelling(throwing, "run_1"), null);
  });
});

// ---- momentFromMemory / questionForMemory --------------------------------------------

describe("momentFromMemory", () => {
  test("title, year and a copy of the participants", () => {
    const ids = ["dad", "mom"];
    const res = {
      memory: { id: "hymn_books", familyId: "f", title: "The hymn books", year: 1988, participantIds: ids },
      props: [],
    };
    const m = momentFromMemory(res);
    assert.deepEqual(m, { title: "The hymn books", year: 1988, participantIds: ["dad", "mom"], tellerIds: [] });
    m.participantIds.push("x");
    assert.deepEqual(ids, ["dad", "mom"]);
  });

  test("no participants listed", () => {
    const res = { memory: { id: "m", familyId: "f", title: "T", year: 1970 } };
    assert.deepEqual(momentFromMemory(res), { title: "T", year: 1970, participantIds: [], tellerIds: [] });
  });

  test("tellers from the tellings, in order, once each", () => {
    const res = {
      memory: {
        id: "m",
        familyId: "f",
        title: "T",
        year: 1970,
        tellings: [
          { authorId: "dad", text: "a" },
          { authorId: "mom", text: "b" },
          { authorId: "dad", text: "c" },
          { authorId: "", text: "d" },
        ],
      },
    };
    assert.deepEqual(momentFromMemory(res).tellerIds, ["dad", "mom"]);
  });
});

describe("momentFromSummary / momentsFromThreads", () => {
  const threads = {
    people: [],
    moments: [
      { id: "hymn", title: "The hymn books", year: 1988, participantIds: ["dad", "mom"], tellingAuthorIds: ["dad", "mom"] },
      { id: "bike", title: "Sam's first bike", year: 1997, participantIds: ["sam"] },
      { id: "hymn", title: "A duplicate id", year: 2000, participantIds: [] },
      { id: "", title: "No id", year: 2001, participantIds: [] },
    ],
  };

  test("a summary gives the same shape as a memory", () => {
    const s = threads.moments[0];
    const m = momentFromSummary(s);
    assert.deepEqual(m, { title: "The hymn books", year: 1988, participantIds: ["dad", "mom"], tellerIds: ["dad", "mom"] });
    m.participantIds.push("x");
    assert.deepEqual(s.participantIds, ["dad", "mom"]);
  });

  test("no tellingAuthorIds means no tellers yet", () => {
    assert.deepEqual(momentFromSummary(threads.moments[1]).tellerIds, []);
  });

  test("moments by id; the first of a duplicate id wins; blank ids are skipped", () => {
    const all = momentsFromThreads(threads);
    assert.deepEqual([...all.keys()], ["hymn", "bike"]);
    assert.equal(all.get("hymn").title, "The hymn books");
    assert.equal(all.get("nope"), undefined);
  });

  test("no threads (the request failed) is null, an empty family an empty map", () => {
    assert.equal(momentsFromThreads(null), null);
    assert.equal(momentsFromThreads(undefined), null);
    assert.equal(momentsFromThreads({ people: [] }), null);
    assert.equal(momentsFromThreads({ people: [], moments: [] }).size, 0);
  });
});

describe("hasTold", () => {
  test("only someone among the tellers", () => {
    const m = { tellerIds: ["dad", "mom"] };
    assert.equal(hasTold(m, "mom"), true);
    assert.equal(hasTold(m, "sam"), false);
    assert.equal(hasTold(m, ""), false);
    assert.equal(hasTold(null, "mom"), false);
  });
});

describe("questionForMemory", () => {
  const qs = [
    { id: "q1", familyId: "f", personId: "p", text: "Free question", memoryId: null },
    { id: "q2", familyId: "f", personId: "p", text: "About the hymn books", memoryId: "hymn_books" },
  ];
  test("finds the question about that moment", () => {
    assert.equal(questionForMemory(qs, "hymn_books")?.id, "q2");
  });
  test("null when none matches or no moment", () => {
    assert.equal(questionForMemory(qs, "other"), null);
    assert.equal(questionForMemory(qs, null), null);
    assert.equal(questionForMemory([], "hymn_books"), null);
  });
});

// ---- withRunGone ------------------------------------------------------------------

async function collect(iterable) {
  const out = [];
  for await (const m of iterable) out.push(m);
  return out;
}

describe("withRunGone", () => {
  const signal = new AbortController().signal;

  test("passes events and answers through", async () => {
    const answered = [];
    const events = [
      { event: "plan", data: { todos: [] } },
      { event: "done", data: { memoryId: "m1" } },
    ];
    const src = withRunGone({
      async *stream() {
        yield* events;
      },
      answer: async (body) => void answered.push(body),
    });
    assert.deepEqual(await collect(src.stream(signal)), events);
    await src.answer({ kind: "answer", text: "hi" });
    assert.deepEqual(answered, [{ kind: "answer", text: "hi" }]);
  });

  test("a 404 ends the run with an error event", async () => {
    const src = withRunGone({
      async *stream() {
        throw Object.assign(new Error("Not found"), { status: 404 });
      },
      answer: async () => {},
    });
    assert.deepEqual(await collect(src.stream(signal)), [
      { event: "error", data: { message: RUN_GONE_MESSAGE } },
    ]);
  });

  test("a 404 tells onGone first, and only a 404 does", async () => {
    const calls = [];
    const gone = withRunGone(
      {
        async *stream() {
          throw Object.assign(new Error("Not found"), { status: 404 });
        },
        answer: async () => {},
      },
      "gone",
      () => calls.push("gone"),
    );
    const seen = [];
    for await (const m of gone.stream(signal)) {
      calls.push("event");
      seen.push(m);
    }
    assert.deepEqual(calls, ["gone", "event"]);
    assert.deepEqual(seen, [{ event: "error", data: { message: "gone" } }]);

    let told = 0;
    const fine = withRunGone(
      {
        async *stream() {
          yield { event: "done", data: { memoryId: "m1" } };
        },
        answer: async () => {},
      },
      undefined,
      () => told++,
    );
    await collect(fine.stream(signal));
    const dropped = withRunGone(
      {
        async *stream() {
          throw Object.assign(new Error("dropped"), { status: 0 });
        },
        answer: async () => {},
      },
      undefined,
      () => told++,
    );
    await assert.rejects(collect(dropped.stream(signal)));
    assert.equal(told, 0);
  });

  test("other failures still throw so the hook reconnects", async () => {
    for (const err of [Object.assign(new Error("dropped"), { status: 0 }), new Error("plain"), "string"]) {
      const src = withRunGone({
        async *stream() {
          yield { event: "plan", data: { todos: [] } };
          throw err;
        },
        answer: async () => {},
      });
      const seen = [];
      await assert.rejects(async () => {
        for await (const m of src.stream(signal)) seen.push(m);
      });
      assert.equal(seen.length, 1);
    }
  });
});

// ---- the sent draft ("Tell it again") ---------------------------------------------

/** A copy of `o` without `keys`. */
function withoutKeys(o, keys) {
  return Object.fromEntries(Object.entries(o).filter(([k]) => !keys.includes(k)));
}

const SENT = {
  text: "We met at the dance hall.",
  year: 1987,
  question: "How did you two meet?",
  memoryId: null,
  participantIds: ["p_me", "p_rose"],
  photoCount: 2,
};

describe("sentDraftFromSubmit", () => {
  test("keeps what was sent", () => {
    assert.deepEqual(
      sentDraftFromSubmit(
        { text: "We met at the dance hall.", year: 1987, question: "How did you two meet?", participantIds: ["p_me", "p_rose"] },
        2,
      ),
      SENT,
    );
  });

  test("trims the question to null when blank, and keeps a second telling's moment", () => {
    assert.equal(sentDraftFromSubmit({ text: "t", year: 1990, question: "   " }, 0).question, null);
    assert.equal(sentDraftFromSubmit({ text: "t", year: 1990, question: null }, 0).question, null);
    assert.equal(sentDraftFromSubmit({ text: "t", year: 1990 }, 0).question, null);
    assert.equal(sentDraftFromSubmit({ text: "t", year: 1990, question: "  Who? " }, 0).question, "Who?");
    const second = sentDraftFromSubmit({ text: "t", year: 1990, memoryId: "m_1" }, 0);
    assert.equal(second.memoryId, "m_1");
    assert.deepEqual(second.participantIds, []);
    assert.equal(sentDraftFromSubmit({ text: "t", year: 1990, memoryId: "  " }, 0).memoryId, null);
  });

  test("dedupes participants, dropping blanks", () => {
    const d = sentDraftFromSubmit({ text: "t", year: 1990, participantIds: ["a", "b", "a", "", "b", "c"] }, 0);
    assert.deepEqual(d.participantIds, ["a", "b", "c"]);
  });

  test("floors the photo count and never goes below zero", () => {
    assert.equal(sentDraftFromSubmit({ text: "t", year: 1990 }, 2.7).photoCount, 2);
    assert.equal(sentDraftFromSubmit({ text: "t", year: 1990 }, -3).photoCount, 0);
    assert.equal(sentDraftFromSubmit({ text: "t", year: 1990 }, Number.NaN).photoCount, 0);
    assert.equal(sentDraftFromSubmit({ text: "t", year: 1990 }, Number.POSITIVE_INFINITY).photoCount, 0);
  });
});

describe("saveSentDraft and loadSentDraft", () => {
  test("round trip under the run's key", () => {
    const s = memoryStorage();
    saveSentDraft(s, "run_1", SENT);
    assert.deepEqual([...s.map.keys()], [DRAFT_KEY_PREFIX + "run_1"]);
    assert.equal(DRAFT_KEY_PREFIX, "stitch.draft.");
    assert.deepEqual(loadSentDraft(s, "run_1"), SENT);
    assert.equal(loadSentDraft(s, "run_2"), null);
  });

  test("a second telling round-trips", () => {
    const s = memoryStorage();
    const d = { ...SENT, memoryId: "m_1", participantIds: [], question: null, photoCount: 0 };
    saveSentDraft(s, "r", d);
    assert.deepEqual(loadSentDraft(s, "r"), d);
  });

  test("no storage, no run id, or a missing key: nothing", () => {
    assert.equal(loadSentDraft(null, "run_1"), null);
    saveSentDraft(null, "run_1", SENT); // does not throw
    const s = memoryStorage();
    saveSentDraft(s, "", SENT);
    assert.equal(s.map.size, 0);
    assert.equal(loadSentDraft(s, ""), null);
    assert.equal(loadSentDraft(memoryStorage(), "nope"), null);
  });

  test("a storage that throws never throws here", () => {
    assert.doesNotThrow(() => saveSentDraft(throwing, "run_1", SENT));
    assert.equal(loadSentDraft(throwing, "run_1"), null);
  });

  test("anything malformed is null", () => {
    const bad = [
      "not json",
      "{",
      "null",
      "[]",
      "42",
      JSON.stringify({ ...SENT, text: 5 }),
      JSON.stringify({ ...SENT, year: "1987" }),
      JSON.stringify({ ...SENT, year: null }),
      // JSON has no Infinity: it arrives as null, and a non-finite year is refused.
      JSON.stringify({ ...SENT, year: Number.POSITIVE_INFINITY }),
      JSON.stringify({ ...SENT, photoCount: -1 }),
      JSON.stringify({ ...SENT, photoCount: "2" }),
      JSON.stringify({ ...SENT, question: 3 }),
      JSON.stringify({ ...SENT, memoryId: {} }),
      JSON.stringify({ ...SENT, participantIds: "p_me" }),
      JSON.stringify({ ...SENT, participantIds: ["p_me", 7] }),
      JSON.stringify({ ...SENT, participantIds: [null] }),
      JSON.stringify(withoutKeys(SENT, ["participantIds"])),
    ];
    for (const raw of bad) {
      const s = memoryStorage();
      s.map.set(DRAFT_KEY_PREFIX + "r", raw);
      assert.equal(loadSentDraft(s, "r"), null, raw);
    }
  });

  test("a missing question or moment reads as null", () => {
    const s = memoryStorage();
    const rest = withoutKeys(SENT, ["question", "memoryId"]);
    s.map.set(DRAFT_KEY_PREFIX + "r", JSON.stringify(rest));
    assert.deepEqual(loadSentDraft(s, "r"), { ...rest, question: null, memoryId: null });
  });
});

describe("draftFromSent", () => {
  test("year becomes a string and a null question an empty one", () => {
    assert.deepEqual(draftFromSent({ ...SENT, question: null }), {
      text: "We met at the dance hall.",
      year: "1987",
      question: "",
      memoryId: null,
      participantIds: ["p_me", "p_rose"],
    });
    assert.deepEqual(draftFromSent({ ...SENT, memoryId: "m_1" }), {
      text: "We met at the dance hall.",
      year: "1987",
      question: "How did you two meet?",
      memoryId: "m_1",
      participantIds: ["p_me", "p_rose"],
    });
  });

  test("hands back its own participant list", () => {
    const d = { ...SENT, participantIds: ["a"] };
    const out = draftFromSent(d);
    out.participantIds.push("b");
    assert.deepEqual(d.participantIds, ["a"]);
  });
});

describe("restoreNotice", () => {
  const LEAD = "The studio couldn't finish your last one, so here it is again. Change anything you like and send it.";

  test("no photos sent, or all of them back: just the lead", () => {
    assert.equal(restoreNotice(0, null), LEAD);
    assert.equal(restoreNotice(0, 0), LEAD);
    assert.equal(restoreNotice(2, 2), LEAD);
    assert.equal(restoreNotice(1, 1), LEAD);
  });

  test("photos that did not come back are asked for again", () => {
    assert.equal(restoreNotice(2, null), `${LEAD} Your photos didn't come along; add them again.`);
    assert.equal(restoreNotice(3, 1), `${LEAD} Your photos didn't come along; add them again.`);
    assert.equal(restoreNotice(1, null), `${LEAD} Your photo didn't come along; add it again.`);
    assert.equal(restoreNotice(1, 0), `${LEAD} Your photo didn't come along; add it again.`);
  });
});

// ---- carrying the story into another family's form ---------------------------------

function fullStorage() {
  const map = new Map();
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => void map.set(k, String(v)),
    removeItem: (k) => void map.delete(k),
  };
}

/** What readCarry gives back for a story and year alone. */
const story = (text, year) => ({ text, year, participantIds: null, questionId: null, question: "", photoCount: 0 });

describe("saveCarry / readCarry / clearCarry", () => {
  test("the story and year reach that family's form, and only that family's", () => {
    const st = fullStorage();
    saveCarry(st, { familyId: "fam2", text: "The green bike", year: "1997" });
    assert.deepEqual(readCarry(st, "fam2"), story("The green bike", "1997"));
    assert.equal(readCarry(st, "fam1"), null);
  });

  test("reading leaves it for a second read (a state initializer can run twice); clearing drops it", () => {
    const st = fullStorage();
    saveCarry(st, { familyId: "f", text: "t", year: "" });
    assert.deepEqual(readCarry(st, "f"), story("t", ""));
    assert.deepEqual(readCarry(st, "f"), story("t", ""));
    clearCarry(st);
    assert.equal(readCarry(st, "f"), null);
    assert.equal(st.map.size, 0);
  });

  test("nothing typed clears an older carry instead of keeping it", () => {
    const st = fullStorage();
    saveCarry(st, { familyId: "f", text: "old", year: "" });
    saveCarry(st, { familyId: "g", text: "   ", year: "" });
    assert.equal(st.map.has(CARRY_KEY), false);
  });

  test("a year that is not a year is dropped; a story with only a bad year is nothing", () => {
    const st = fullStorage();
    st.setItem(CARRY_KEY, JSON.stringify({ familyId: "f", text: "t", year: "19x7" }));
    assert.deepEqual(readCarry(st, "f"), story("t", ""));
    st.setItem(CARRY_KEY, JSON.stringify({ familyId: "f", text: " ", year: "abc" }));
    assert.equal(readCarry(st, "f"), null);
  });

  test("unreadable, wrong-shaped or missing values are null", () => {
    const st = fullStorage();
    assert.equal(readCarry(st, "f"), null);
    for (const raw of ["{", "[]", "null", JSON.stringify({ familyId: "f", text: 3, year: "" })]) {
      st.setItem(CARRY_KEY, raw);
      assert.equal(readCarry(st, "f"), null, raw);
    }
    assert.equal(readCarry(null, "f"), null);
    assert.equal(readCarry(st, ""), null);
  });

  test("storage that throws never throws out", () => {
    const bad = {
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
    assert.doesNotThrow(() => saveCarry(bad, { familyId: "f", text: "t", year: "" }));
    assert.equal(readCarry(bad, "f"), null);
    assert.doesNotThrow(() => clearCarry(bad));
    assert.doesNotThrow(() => clearCarry(null));
  });
});

describe("carryInto", () => {
  const typed = { text: "The green bike", year: "1997", participantIds: ["sam", "dad"] };

  test("into a general question: story, year and people all come along", () => {
    const initial = { question: "How did you two meet?", memoryId: null };
    assert.deepEqual(carryInto(initial, typed, false), {
      question: "How did you two meet?",
      memoryId: null,
      text: "The green bike",
      year: "1997",
      participantIds: ["sam", "dad"],
    });
  });

  test("into a second telling: only the story; the moment keeps its year and people", () => {
    const initial = { question: "Q", memoryId: "hymn", year: "1988", participantIds: ["dad", "mom"] };
    assert.deepEqual(carryInto(initial, typed, true), { ...initial, text: "The green bike" });
  });

  test("a form that already has its own year or people keeps them", () => {
    const initial = { year: "2001", participantIds: ["mom"] };
    assert.deepEqual(carryInto(initial, typed, false), { year: "2001", participantIds: ["mom"], text: "The green bike" });
  });

  test("nothing carried returns the same starting values (even undefined)", () => {
    const none = { text: "", year: "", participantIds: null };
    const initial = { question: "Q" };
    assert.equal(carryInto(initial, none, false), initial);
    assert.equal(carryInto(undefined, none, false), undefined);
    assert.equal(carryInto(undefined, { ...none, participantIds: [] }, false), undefined);
  });

  test("people are deduped and blanks dropped", () => {
    const res = carryInto(undefined, { text: "", year: "", participantIds: ["a", "", "a", "b"] }, false);
    assert.deepEqual(res, { participantIds: ["a", "b"] });
  });

  test("a typed question comes back into a new memory with no question of its own", () => {
    const typed = { text: "t", year: "", participantIds: null, question: "Who taught you to swim?" };
    assert.deepEqual(carryInto(undefined, typed, false), { text: "t", question: "Who taught you to swim?" });
    // A picked question's own text wins; a second telling never takes one.
    assert.deepEqual(carryInto({ question: "Picked?" }, typed, false), { question: "Picked?", text: "t" });
    assert.deepEqual(carryInto(undefined, typed, true), { text: "t" });
    // Blank questions are nothing.
    assert.equal(carryInto(undefined, { text: "", year: "", participantIds: null, question: "  " }, false), undefined);
  });
});

// ---- the add-relative round trip ----------------------------------------------

describe("addRelativeHref", () => {
  test("goes to the family's add-relative page, coming back to this family's /add", () => {
    assert.equal(addRelativeHref("F"), "/families/F/people/new?next=%2Fadd%3Ffamily%3DF");
    assert.equal(
      addRelativeHref("6650c0ffee"),
      "/families/6650c0ffee/people/new?next=%2Fadd%3Ffamily%3D6650c0ffee",
    );
  });

  test("an odd family id is encoded in the path and inside next, so it survives the trip", () => {
    const href = addRelativeHref("a&b c");
    assert.equal(href, "/families/a%26b%20c/people/new?next=%2Fadd%3Ffamily%3Da%2526b%2520c");
    const next = new URL(href, "http://x").searchParams.get("next");
    assert.equal(next, "/add?family=a%26b%20c");
    assert.equal(readAddQuery(next.slice(next.indexOf("?"))).familyId, "a&b c");
  });

  test("next is a path safeNext accepts (the add-relative page drops any other)", () => {
    const next = new URL(addRelativeHref("fam_1"), "http://x").searchParams.get("next");
    assert.equal(safeNext(next), "/add?family=fam_1");
    assert.equal(safeNext(new URL(addRelativeHref("a&b c"), "http://x").searchParams.get("next")), "/add?family=a%26b%20c");
  });
});

describe("addedFromQuery / withoutAdded", () => {
  test("reads ?added=, null when missing or empty", () => {
    assert.equal(addedFromQuery("?family=f&added=p1"), "p1");
    assert.equal(addedFromQuery(new URLSearchParams("added=p%202")), "p 2");
    assert.equal(addedFromQuery("?family=f"), null);
    assert.equal(addedFromQuery("?added="), null);
    assert.equal(addedFromQuery(""), null);
    assert.equal(addedFromQuery(null), null);
  });

  test("drops added and keeps family, memoryId, run and retry", () => {
    assert.equal(withoutAdded("?family=f&added=p1"), "/add?family=f");
    assert.equal(
      withoutAdded("?family=f&added=p1&memoryId=m&run=r&retry=x"),
      "/add?family=f&memoryId=m&run=r&retry=x",
    );
    assert.equal(withoutAdded(new URLSearchParams("added=p1&added=p2")), "/add");
    assert.equal(withoutAdded("?family=a%26b&added=p"), "/add?family=a%26b");
    assert.equal(withoutAdded(""), "/add");
    assert.equal(withoutAdded(null), "/add");
  });
});

describe("preselectAdded", () => {
  const known = ["me", "dad", "lily", "new_aunt"];

  test("ticks the person just added, after the people carried over", () => {
    assert.deepEqual(preselectAdded(["me", "dad"], "new_aunt", known), ["me", "dad", "new_aunt"]);
    assert.deepEqual(preselectAdded(null, "new_aunt", known), ["new_aunt"]);
  });

  test("an unknown added id is ignored; nobody to tick is null", () => {
    assert.deepEqual(preselectAdded(["dad"], "someone_else", known), ["dad"]);
    assert.equal(preselectAdded(null, "someone_else", known), null);
    assert.equal(preselectAdded(null, null, known), null);
    assert.equal(preselectAdded([], "", known), null);
  });

  test("carried people no longer in the family are dropped, and nobody is ticked twice", () => {
    assert.deepEqual(preselectAdded(["dad", "gone", "dad"], "dad", known), ["dad"]);
  });
});

describe("carriedPhotosNote", () => {
  test("says nothing without photos, one line with them", () => {
    assert.equal(carriedPhotosNote(0), null);
    assert.equal(carriedPhotosNote(-2), null);
    assert.equal(carriedPhotosNote(Number.NaN), null);
    assert.match(carriedPhotosNote(1), /Your photo didn’t come along, so add it again/);
    assert.match(carriedPhotosNote(3), /Your photos didn’t come along, so add them again/);
    assert.doesNotMatch(carriedPhotosNote(3), /\n/);
  });
});

describe("the extended carry", () => {
  test("keeps the people, the picked question and the photo count for the same family", () => {
    const st = fullStorage();
    saveCarry(st, {
      familyId: "f",
      text: "The lake",
      year: "1979",
      participantIds: ["dad", "", "dad", "lily"],
      questionId: "q_lake",
      question: null,
      photoCount: 2,
    });
    assert.deepEqual(readCarry(st, "f"), {
      text: "The lake",
      year: "1979",
      participantIds: ["dad", "lily"],
      questionId: "q_lake",
      question: "",
      photoCount: 2,
    });
  });

  test("a typed question travels as text", () => {
    const st = fullStorage();
    saveCarry(st, { familyId: "f", text: "", year: "", question: "Who taught you to swim?" });
    assert.deepEqual(readCarry(st, "f"), { ...story("", ""), question: "Who taught you to swim?" });
  });

  test("people alone, a picked question alone or photos alone are worth keeping", () => {
    for (const extra of [{ participantIds: ["dad"] }, { questionId: "q1" }, { photoCount: 1 }]) {
      const st = fullStorage();
      saveCarry(st, { familyId: "f", text: "", year: "", ...extra });
      assert.ok(st.map.has(CARRY_KEY), JSON.stringify(extra));
      assert.notEqual(readCarry(st, "f"), null, JSON.stringify(extra));
    }
    const st = fullStorage();
    saveCarry(st, { familyId: "f", text: "old", year: "" });
    saveCarry(st, { familyId: "f", text: " ", year: "", participantIds: [], questionId: "", question: " ", photoCount: 0 });
    assert.equal(st.map.has(CARRY_KEY), false);
  });

  test("a carry saved before the new fields existed still reads, with none of them", () => {
    const st = fullStorage();
    st.setItem(CARRY_KEY, JSON.stringify({ familyId: "f", text: "The green bike", year: "1997" }));
    assert.deepEqual(readCarry(st, "f"), story("The green bike", "1997"));
  });

  test("wrong-shaped new fields read as none instead of spoiling the story", () => {
    const st = fullStorage();
    st.setItem(
      CARRY_KEY,
      JSON.stringify({ familyId: "f", text: "t", year: "", participantIds: "dad", questionId: 7, question: [], photoCount: "2" }),
    );
    assert.deepEqual(readCarry(st, "f"), story("t", ""));
    st.setItem(CARRY_KEY, JSON.stringify({ familyId: "f", text: "t", year: "", participantIds: ["a", 3, "b"], photoCount: 2.7 }));
    assert.deepEqual(readCarry(st, "f"), { ...story("t", ""), participantIds: ["a", "b"], photoCount: 2 });
  });

  test("the round trip, end to end: carried draft plus ?added= give the form back", () => {
    const st = fullStorage();
    // On /add?family=f: typed, ticked Dad, picked a general question, attached a photo.
    saveCarry(st, {
      familyId: "f",
      text: "Grandma's kitchen",
      year: "1984",
      participantIds: ["dad"],
      questionId: "q_kitchen",
      question: null,
      photoCount: 1,
    });
    // Back on /add?family=f&added=aunt_rosa, after Aunt Rosa was added.
    const c = readCarry(st, "f");
    const ids = preselectAdded(c.participantIds, addedFromQuery("?family=f&added=aunt_rosa"), ["me", "dad", "aunt_rosa"]);
    const initial = carryInto(
      { question: "What did her kitchen smell like?", memoryId: null },
      { text: c.text, year: c.year, participantIds: ids, question: c.question },
      false,
    );
    assert.deepEqual(initial, {
      question: "What did her kitchen smell like?",
      memoryId: null,
      text: "Grandma's kitchen",
      year: "1984",
      participantIds: ["dad", "aunt_rosa"],
    });
    assert.equal(c.questionId, "q_kitchen");
    assert.ok(carriedPhotosNote(c.photoCount));
    assert.equal(withoutAdded("?family=f&added=aunt_rosa"), "/add?family=f");
  });
});

// ---- the picked question in the URL -------------------------------------------------

describe("questionIdFromQuery / withQuestionId", () => {
  test("reads ?questionId=, null when missing or empty", () => {
    assert.equal(questionIdFromQuery("?family=f&questionId=q1"), "q1");
    assert.equal(questionIdFromQuery(new URLSearchParams("questionId=q%202")), "q 2");
    assert.equal(questionIdFromQuery("?questionId="), null);
    assert.equal(questionIdFromQuery("?family=f"), null);
    assert.equal(questionIdFromQuery(""), null);
    assert.equal(questionIdFromQuery(null), null);
  });

  test("sets the question, keeping family, memoryId and the rest in place", () => {
    assert.equal(withQuestionId("?family=f", "q1"), "/add?family=f&questionId=q1");
    assert.equal(withQuestionId("?family=f&memoryId=m", "q1"), "/add?family=f&memoryId=m&questionId=q1");
    assert.equal(withQuestionId("", "q1"), "/add?questionId=q1");
    assert.equal(withQuestionId(null, "q1"), "/add?questionId=q1");
  });

  test("replaces an earlier pick instead of adding a second one", () => {
    const out = withQuestionId("?family=f&questionId=old", "new");
    assert.equal(out, "/add?family=f&questionId=new");
    assert.equal(questionIdFromQuery(out.slice("/add".length)), "new");
  });

  test("null or empty drops it; nothing left is plain /add", () => {
    assert.equal(withQuestionId("?family=f&questionId=q1", null), "/add?family=f");
    assert.equal(withQuestionId("?questionId=q1&family=f", ""), "/add?family=f");
    assert.equal(withQuestionId("?questionId=q1", null), "/add");
    assert.equal(withQuestionId("", null), "/add");
  });

  test("odd ids are encoded and read back as they were", () => {
    const id = "a&b=c#d e";
    const out = withQuestionId(new URLSearchParams({ family: "f" }), id);
    assert.equal(questionIdFromQuery(out.slice("/add".length)), id);
    assert.equal(readAddQuery(out.slice("/add".length)).familyId, "f");
  });
});

// ---- the draft kept for a reload ----------------------------------------------------

describe("saveTypedDraft / readTypedDraft / clearTypedDraft", () => {
  const draft = (over = {}) => ({
    familyId: "f",
    memoryId: null,
    questionId: null,
    text: "The red bike",
    year: "1997",
    participantIds: ["dad"],
    question: "",
    photoCount: 0,
    ...over,
  });

  test("keeps a draft per family and reads it back whole", () => {
    const st = fullStorage();
    saveTypedDraft(st, draft());
    saveTypedDraft(st, draft({ familyId: "g", text: "Other family", year: "", participantIds: [] }));
    assert.deepEqual(readTypedDraft(st, "f"), draft());
    assert.equal(readTypedDraft(st, "g").text, "Other family");
    assert.equal(readTypedDraft(st, "h"), null);
    assert.deepEqual(Object.keys(JSON.parse(st.getItem(DRAFT_KEY))).sort(), ["f", "g"]);
  });

  test("a newer save replaces the family's draft", () => {
    const st = fullStorage();
    saveTypedDraft(st, draft());
    saveTypedDraft(st, draft({ text: "The red bike, and the bell", questionId: "q1", photoCount: 2 }));
    assert.deepEqual(readTypedDraft(st, "f"), draft({ text: "The red bike, and the bell", questionId: "q1", photoCount: 2 }));
  });

  test("a second telling's draft keeps its moment and question", () => {
    const st = fullStorage();
    saveTypedDraft(st, draft({ memoryId: "m1", year: "", participantIds: [], text: "I remember it differently" }));
    const got = readTypedDraft(st, "f");
    assert.equal(got.memoryId, "m1");
    assert.equal(got.text, "I remember it differently");
  });

  test("nothing typed drops the family's draft and leaves the others", () => {
    const st = fullStorage();
    saveTypedDraft(st, draft());
    saveTypedDraft(st, draft({ familyId: "g" }));
    saveTypedDraft(st, draft({ text: "  ", year: "", participantIds: [], questionId: "q1" }));
    assert.equal(readTypedDraft(st, "f"), null);
    assert.ok(readTypedDraft(st, "g"));
    saveTypedDraft(st, draft({ familyId: "g", text: "", year: "", participantIds: [] }));
    assert.equal(st.getItem(DRAFT_KEY), null, "the key goes once no family has a draft");
  });

  test("people, a typed question or photos alone are worth keeping", () => {
    const st = fullStorage();
    const blank = { text: "", year: "", participantIds: [] };
    saveTypedDraft(st, draft({ ...blank, participantIds: ["ana"] }));
    assert.deepEqual(readTypedDraft(st, "f").participantIds, ["ana"]);
    saveTypedDraft(st, draft({ ...blank, question: "How did you meet?" }));
    assert.equal(readTypedDraft(st, "f").question, "How did you meet?");
    saveTypedDraft(st, draft({ ...blank, photoCount: 3 }));
    assert.equal(readTypedDraft(st, "f").photoCount, 3);
  });

  test("clear drops one family's draft only, and sending clears it", () => {
    const st = fullStorage();
    saveTypedDraft(st, draft());
    saveTypedDraft(st, draft({ familyId: "g" }));
    clearTypedDraft(st, "f");
    assert.equal(readTypedDraft(st, "f"), null);
    assert.ok(readTypedDraft(st, "g"));
    clearTypedDraft(st, "g");
    assert.equal(st.getItem(DRAFT_KEY), null);
    assert.doesNotThrow(() => clearTypedDraft(st, "nobody"));
    assert.doesNotThrow(() => clearTypedDraft(null, "f"));
  });

  test("does not touch the carry or the per-run drafts", () => {
    const st = fullStorage();
    saveCarry(st, { familyId: "f", text: "carried", year: "" });
    saveSentDraft(st, "run1", sentDraftFromSubmit({ text: "sent", year: 1990 }, 0));
    saveTypedDraft(st, draft());
    clearTypedDraft(st, "f");
    assert.equal(readCarry(st, "f").text, "carried");
    assert.equal(loadSentDraft(st, "run1").text, "sent");
    assert.notEqual(DRAFT_KEY, CARRY_KEY);
    assert.ok(!DRAFT_KEY.startsWith(DRAFT_KEY_PREFIX));
  });

  test("unreadable, wrong-shaped or foreign values read as nothing", () => {
    const st = fullStorage();
    st.setItem(DRAFT_KEY, "{not json");
    assert.equal(readTypedDraft(st, "f"), null);
    st.setItem(DRAFT_KEY, JSON.stringify(["f"]));
    assert.equal(readTypedDraft(st, "f"), null);
    st.setItem(DRAFT_KEY, JSON.stringify({ f: { text: 7 } }));
    assert.equal(readTypedDraft(st, "f"), null);
    st.setItem(
      DRAFT_KEY,
      JSON.stringify({ f: { text: "t", year: "19x7", participantIds: ["a", 3, "a"], question: 5, photoCount: "2", memoryId: 9 } }),
    );
    assert.deepEqual(readTypedDraft(st, "f"), {
      familyId: "f",
      memoryId: null,
      questionId: null,
      text: "t",
      year: "",
      participantIds: ["a"],
      question: "",
      photoCount: 0,
    });
    // A broken map is replaced by the next save rather than blocking it.
    st.setItem(DRAFT_KEY, "{not json");
    saveTypedDraft(st, draft());
    assert.deepEqual(readTypedDraft(st, "f"), draft());
    assert.equal(readTypedDraft(st, ""), null);
  });

  test("storage that throws, or none, never throws out", () => {
    const bad = {
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
    assert.doesNotThrow(() => saveTypedDraft(bad, draft()));
    assert.equal(readTypedDraft(bad, "f"), null);
    assert.doesNotThrow(() => clearTypedDraft(bad, "f"));
    assert.doesNotThrow(() => saveTypedDraft(null, draft()));
    assert.equal(readTypedDraft(null, "f"), null);
  });
});

describe("draftForForm", () => {
  const kept = (over = {}) => ({
    familyId: "f",
    memoryId: null,
    questionId: null,
    text: "t",
    year: "",
    participantIds: [],
    question: "",
    photoCount: 0,
    ...over,
  });

  test("a new memory's draft opens on the plain form", () => {
    const d = kept();
    assert.equal(draftForForm(d, { memoryId: null, questionId: null }), d);
  });

  test("a draft with a picked question opens with the same question, or with none in the URL", () => {
    const d = kept({ questionId: "q1" });
    assert.equal(draftForForm(d, { memoryId: null, questionId: "q1" }), d);
    assert.equal(draftForForm(d, { memoryId: null, questionId: null }), d);
    assert.equal(draftForForm(d, { memoryId: null, questionId: "q2" }), null);
    assert.equal(draftForForm(kept(), { memoryId: null, questionId: "q2" }), null);
  });

  test("a second telling's draft opens only on that moment", () => {
    const d = kept({ memoryId: "m1" });
    assert.equal(draftForForm(d, { memoryId: "m1", questionId: null }), d);
    assert.equal(draftForForm(d, { memoryId: "m2", questionId: null }), null);
    assert.equal(draftForForm(d, { memoryId: null, questionId: null }), null);
    assert.equal(draftForForm(kept(), { memoryId: "m1", questionId: null }), null);
  });

  test("no draft is no draft", () => {
    assert.equal(draftForForm(null, { memoryId: null, questionId: null }), null);
  });

  test("a reload, end to end: the pick in the URL and the draft give the form back", () => {
    const st = fullStorage();
    // On /add?family=f, the person picks q_wedding (about moment m1) and types.
    const url = withQuestionId("?family=f", "q_wedding");
    saveTypedDraft(st, kept({ questionId: "q_wedding", text: "His flower fell off" }));
    // Reload: the same URL, the same tab.
    const search = url.slice("/add".length);
    const q = readAddQuery(search);
    const d = draftForForm(readTypedDraft(st, q.familyId), { memoryId: q.memoryId, questionId: questionIdFromQuery(search) });
    assert.equal(d.text, "His flower fell off");
    assert.equal(d.questionId, "q_wedding");
    // Sent: the draft is gone and the run URL carries no question.
    clearTypedDraft(st, "f");
    assert.equal(readTypedDraft(st, "f"), null);
    assert.equal(questionIdFromQuery(runHref({ familyId: "f", runId: "r" }).slice("/add".length)), null);
  });
});

// ---- open runs ------------------------------------------------------------------

describe("rememberOpenRun / markRunWaiting / forgetOpenRun / openRunsFor", () => {
  const started = (runId, familyId = "f", startedAt = 1000, title = null) => ({ familyId, runId, title, startedAt });

  test("round trip: a remembered run reads back, not waiting", () => {
    const st = fullStorage();
    rememberOpenRun(st, started("r1", "f", 1000, "  Sam's wedding "));
    assert.deepEqual(openRunsFor(st, "f"), [
      { familyId: "f", runId: "r1", title: "Sam's wedding", startedAt: 1000, waiting: null },
    ]);
    assert.ok(st.map.has(OPEN_RUNS_KEY));
  });

  test("newest first", () => {
    const st = fullStorage();
    rememberOpenRun(st, started("old", "f", 1000));
    rememberOpenRun(st, started("new", "f", 3000));
    rememberOpenRun(st, started("mid", "f", 2000));
    assert.deepEqual(
      openRunsFor(st, "f").map((r) => r.runId),
      ["new", "mid", "old"],
    );
  });

  test("each family sees only its own runs", () => {
    const st = fullStorage();
    rememberOpenRun(st, started("ra", "fa", 1000));
    rememberOpenRun(st, started("rb", "fb", 2000));
    assert.deepEqual(openRunsFor(st, "fa").map((r) => r.runId), ["ra"]);
    assert.deepEqual(openRunsFor(st, "fb").map((r) => r.runId), ["rb"]);
    assert.deepEqual(openRunsFor(st, "fc"), []);
    assert.deepEqual(openRunsFor(st, ""), []);
  });

  test("markRunWaiting sets and clears the wait", () => {
    const st = fullStorage();
    rememberOpenRun(st, started("r1"));
    markRunWaiting(st, "r1", "followup");
    assert.equal(openRunsFor(st, "f")[0].waiting, "followup");
    markRunWaiting(st, "r1", "props");
    assert.equal(openRunsFor(st, "f")[0].waiting, "props");
    markRunWaiting(st, "r1", null);
    assert.equal(openRunsFor(st, "f")[0].waiting, null);
    // The rest of the record is kept.
    assert.deepEqual(openRunsFor(st, "f")[0], { familyId: "f", runId: "r1", title: null, startedAt: 1000, waiting: null });
  });

  test("markRunWaiting leaves alone a run this tab does not remember", () => {
    const st = fullStorage();
    markRunWaiting(st, "stranger", "followup");
    assert.equal(st.map.has(OPEN_RUNS_KEY), false);
    rememberOpenRun(st, started("r1"));
    markRunWaiting(st, "stranger", "merge");
    assert.deepEqual(openRunsFor(st, "f").map((r) => r.runId), ["r1"]);
  });

  test("markRunWaiting ignores a kind it does not know", () => {
    const st = fullStorage();
    rememberOpenRun(st, started("r1"));
    markRunWaiting(st, "r1", "followup");
    markRunWaiting(st, "r1", "nonsense");
    assert.equal(openRunsFor(st, "f")[0].waiting, null);
  });

  test("forgetOpenRun removes the entry (and the key with the last one)", () => {
    const st = fullStorage();
    rememberOpenRun(st, started("r1", "f", 1000));
    rememberOpenRun(st, started("r2", "f", 2000));
    forgetOpenRun(st, "r1");
    assert.deepEqual(openRunsFor(st, "f").map((r) => r.runId), ["r2"]);
    forgetOpenRun(st, "r1"); // twice is fine
    forgetOpenRun(st, "r2");
    assert.deepEqual(openRunsFor(st, "f"), []);
    assert.equal(st.map.has(OPEN_RUNS_KEY), false);
    // A forgotten run cannot be marked back into being.
    markRunWaiting(st, "r2", "followup");
    assert.deepEqual(openRunsFor(st, "f"), []);
  });

  test("junk JSON is ignored, and remembering over it starts afresh", () => {
    const st = fullStorage();
    st.map.set(OPEN_RUNS_KEY, "{not json");
    assert.deepEqual(openRunsFor(st, "f"), []);
    st.map.set(OPEN_RUNS_KEY, JSON.stringify([1, 2]));
    assert.deepEqual(openRunsFor(st, "f"), []);
    st.map.set(OPEN_RUNS_KEY, "null");
    assert.deepEqual(openRunsFor(st, "f"), []);
    rememberOpenRun(st, started("r1"));
    assert.deepEqual(openRunsFor(st, "f").map((r) => r.runId), ["r1"]);
  });

  test("wrong-shaped entries are skipped; the good ones still read", () => {
    const st = fullStorage();
    st.map.set(
      OPEN_RUNS_KEY,
      JSON.stringify({
        good: { familyId: "f", title: "T", startedAt: 5, waiting: "merge" },
        noFamily: { title: "T", startedAt: 5 },
        noTime: { familyId: "f", startedAt: "yesterday" },
        notObject: "r",
        oddWait: { familyId: "f", title: 42, startedAt: 4, waiting: "lunch" },
      }),
    );
    assert.deepEqual(openRunsFor(st, "f"), [
      { familyId: "f", runId: "good", title: "T", startedAt: 5, waiting: "merge" },
      { familyId: "f", runId: "oddWait", title: null, startedAt: 4, waiting: null },
    ]);
  });

  test("a throwing or missing store is tolerated", () => {
    const bad = {
      getItem() {
        throw new Error("SecurityError");
      },
      setItem() {
        throw new Error("QuotaExceededError");
      },
      removeItem() {
        throw new Error("SecurityError");
      },
    };
    assert.doesNotThrow(() => rememberOpenRun(bad, started("r1")));
    assert.doesNotThrow(() => markRunWaiting(bad, "r1", "followup"));
    assert.doesNotThrow(() => forgetOpenRun(bad, "r1"));
    assert.deepEqual(openRunsFor(bad, "f"), []);
    assert.doesNotThrow(() => rememberOpenRun(null, started("r1")));
    assert.doesNotThrow(() => markRunWaiting(null, "r1", "followup"));
    assert.doesNotThrow(() => forgetOpenRun(null, "r1"));
    assert.deepEqual(openRunsFor(null, "f"), []);
    // A store that reads but cannot write keeps nothing and says nothing.
    const readOnly = { ...fullStorage(), setItem: bad.setItem };
    assert.doesNotThrow(() => rememberOpenRun(readOnly, started("r1")));
    assert.deepEqual(openRunsFor(readOnly, "f"), []);
  });

  test("a blank run or family id is not remembered", () => {
    const st = fullStorage();
    rememberOpenRun(st, started("", "f"));
    rememberOpenRun(st, started("r1", ""));
    assert.equal(st.map.has(OPEN_RUNS_KEY), false);
  });

  test("only the newest OPEN_RUNS_MAX are kept", () => {
    const st = fullStorage();
    for (let i = 0; i < OPEN_RUNS_MAX + 3; i++) rememberOpenRun(st, started(`r${i}`, "f", 1000 + i));
    const ids = openRunsFor(st, "f").map((r) => r.runId);
    assert.equal(ids.length, OPEN_RUNS_MAX);
    assert.equal(ids[0], `r${OPEN_RUNS_MAX + 2}`);
    assert.ok(!ids.includes("r0") && !ids.includes("r1") && !ids.includes("r2"));
  });

  test("lives beside the telling slips without touching them", () => {
    const st = fullStorage();
    saveTelling(st, "r1", { question: null, text: "t", year: 1990, photoCount: 0, secondTellingOf: null });
    rememberOpenRun(st, started("r1"));
    forgetOpenRun(st, "r1");
    assert.ok(st.map.has(TELLING_KEY_PREFIX + "r1"));
  });
});

describe("runToResume", () => {
  const r = (runId, startedAt, waiting = null) => ({ familyId: "f", runId, title: null, startedAt, waiting });

  test("the newest waiting run beats a newer one still working", () => {
    assert.equal(runToResume([r("a", 3), r("b", 2, "followup"), r("c", 1, "props")]).runId, "b");
  });

  test("with none waiting, the newest; with none, null", () => {
    assert.equal(runToResume([r("a", 3), r("b", 2)]).runId, "a");
    assert.equal(runToResume([]), null);
  });
});

describe("firstWords / openRunNotice", () => {
  const run = (over = {}) => ({ familyId: "f", runId: "r", title: null, startedAt: 1, waiting: null, ...over });

  test("firstWords clips to n words with an ellipsis", () => {
    assert.equal(firstWords("Every Sunday we squeezed into the green car", 6), "Every Sunday we squeezed into the…");
    assert.equal(firstWords("  Short  one. "), "Short one.");
    assert.equal(firstWords("One two three four five six, seven", 6), "One two three four five six…");
    assert.equal(firstWords("   "), null);
    assert.equal(firstWords(null), null);
  });

  test("waiting: names the run by its title, else the story's first words", () => {
    assert.equal(
      openRunNotice(run({ title: "Sam's wedding", waiting: "followup" }), "ignored"),
      "Your memory ‘Sam's wedding’ is waiting for your answer.",
    );
    assert.equal(
      openRunNotice(run({ waiting: "props" }), "Every Sunday we squeezed into the green car"),
      "Your memory ‘Every Sunday we squeezed into the…’ is waiting for your answer.",
    );
  });

  test("still working, or with nothing to name it by", () => {
    assert.equal(openRunNotice(run(), "The porch light"), "Your memory ‘The porch light’ is still being made.");
    assert.equal(openRunNotice(run({ waiting: "merge" }), null), "Your memory is waiting for your answer.");
    assert.equal(openRunNotice(run(), ""), "Your memory is still being made.");
  });
});

// ---- which family holds a moment -----------------------------------------------

describe("findMomentFamily", () => {
  const notFound = () => Object.assign(new Error("Not found"), { status: 404 });

  test("the remembered family (first in the list) is tried first", async () => {
    const asked = [];
    const found = await findMomentFamily(["home", "other"], async (fid) => {
      asked.push(fid);
      return { id: "m1", in: fid };
    });
    assert.deepEqual(asked, ["home"]);
    assert.deepEqual(found, { familyId: "home", memory: { id: "m1", in: "home" } });
  });

  test("a 404 moves on to the next family", async () => {
    const asked = [];
    const found = await findMomentFamily(["home", "b", "c"], async (fid) => {
      asked.push(fid);
      if (fid !== "c") throw notFound();
      return "moment";
    });
    assert.deepEqual(asked, ["home", "b", "c"]);
    assert.deepEqual(found, { familyId: "c", memory: "moment" });
  });

  test("any other failure rejects, and no later family is asked", async () => {
    const asked = [];
    const boom = Object.assign(new Error("Server error"), { status: 500 });
    await assert.rejects(
      findMomentFamily(["home", "b", "c"], async (fid) => {
        asked.push(fid);
        if (fid === "home") throw notFound();
        throw boom;
      }),
      (e) => e === boom,
    );
    assert.deepEqual(asked, ["home", "b"]);
    // A network failure (no status at all) rejects too.
    await assert.rejects(findMomentFamily(["x"], async () => Promise.reject(new TypeError("fetch failed"))), TypeError);
  });

  test("every family 404s: null", async () => {
    assert.equal(await findMomentFamily(["a", "b"], async () => Promise.reject(notFound())), null);
    assert.equal(await findMomentFamily([], async () => "never"), null);
  });
});

describe("familyAsking", () => {
  const fams = [{ id: "a", name: "A" }, { id: "b", name: "B" }, { id: "c", name: "C" }];

  test("the first family (in order) with an open question", () => {
    assert.equal(familyAsking(fams, [[], [{ id: "q" }], [{ id: "q2" }]]).id, "b");
  });

  test("failures (null) and empty lists are passed over; none asking is null", () => {
    assert.equal(familyAsking(fams, [null, undefined, [{ id: "q" }]]).id, "c");
    assert.equal(familyAsking(fams, [null, [], []]), null);
    assert.equal(familyAsking([], []), null);
  });
});

// ---- the demo control page's prepared telling ---------------------------------

describe("demoDraftCarry", () => {
  const draft = { text: "Grace and I met at the county fair.", year: 1968, title: "The county fair", question: " How did you meet? " };
  const withDraft = (value = JSON.stringify(draft)) => {
    const s = memoryStorage();
    s.setItem(DEMO_DRAFT_KEY, value);
    return s;
  };
  const on = new URLSearchParams("family=fam_1&demoDraft=1");

  test("the key is the one the control page writes", () => {
    assert.equal(DEMO_DRAFT_KEY, "stitch.demo.draft");
  });

  test("with demoDraft=1: the story, year and question as a carry for the family, key left in place", () => {
    const s = withDraft();
    assert.deepEqual(demoDraftCarry(on, s, "fam_1"), {
      familyId: "fam_1",
      text: "Grace and I met at the county fair.",
      year: "1968",
      question: "How did you meet?",
    });
    assert.ok(s.map.has(DEMO_DRAFT_KEY), "reading must not remove it (a state initializer may run twice)");
  });

  test("it becomes a carry the form reads back", () => {
    const session = memoryStorage();
    saveCarry(session, demoDraftCarry(on, withDraft(), "fam_1"));
    const back = readCarry(session, "fam_1");
    assert.equal(back.text, draft.text);
    assert.equal(back.year, "1968");
    assert.equal(back.question, "How did you meet?");
  });

  test("without the param (or any other value) nothing is read", () => {
    const s = withDraft();
    for (const q of ["family=fam_1", "family=fam_1&demoDraft=0", "family=fam_1&demoDraft=true", ""]) {
      assert.equal(demoDraftCarry(new URLSearchParams(q), s, "fam_1"), null, q);
    }
    assert.equal(demoDraftCarry(null, s, "fam_1"), null);
  });

  test("no family, no storage, blocked storage: null", () => {
    assert.equal(demoDraftCarry(on, withDraft(), null), null);
    assert.equal(demoDraftCarry(on, withDraft(), ""), null);
    assert.equal(demoDraftCarry(on, null, "fam_1"), null);
    assert.equal(demoDraftCarry(on, throwing, "fam_1"), null);
  });

  test("missing, unreadable or story-less drafts: null", () => {
    assert.equal(demoDraftCarry(on, memoryStorage(), "fam_1"), null);
    for (const bad of ["", "{", "null", "[]", "42", '"text"', JSON.stringify({ year: 1968 }), JSON.stringify({ text: "   " }), JSON.stringify({ text: 7 })]) {
      assert.equal(demoDraftCarry(on, withDraft(bad), "fam_1"), null, bad);
    }
  });

  test("year: a number or digit string is kept, anything else is dropped; a missing question is empty", () => {
    const carry = (year) => demoDraftCarry(on, withDraft(JSON.stringify({ text: "x", year })), "fam_1");
    assert.equal(carry(1968).year, "1968");
    assert.equal(carry(" 2004 ").year, "2004");
    assert.equal(carry("nineteen").year, "");
    assert.equal(carry(12345).year, "");
    assert.equal(carry(undefined).year, "");
    assert.equal(carry(1968).question, "");
  });

  test("dropDemoDraft removes the key and survives blocked storage", () => {
    const s = withDraft();
    s.removeItem = (k) => void s.map.delete(k);
    dropDemoDraft(s);
    assert.equal(s.map.has(DEMO_DRAFT_KEY), false);
    dropDemoDraft(null);
    dropDemoDraft({ removeItem() { throw new Error("SecurityError"); } });
  });
});
