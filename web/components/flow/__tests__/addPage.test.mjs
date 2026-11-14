// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in addPage.ts; it has type-only imports,
// so no resolve hook is needed.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  RUN_GONE_MESSAGE,
  TELLING_KEY_PREFIX,
  loadTelling,
  momentFromMemory,
  questionForMemory,
  readAddQuery,
  runHref,
  saveTelling,
  tellingFromSubmit,
  withRunGone,
} from "../addPage.ts";

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
    });
  });

  test("works without the leading ?", () => {
    assert.deepEqual(readAddQuery("family=f&run=r"), { familyId: "f", memoryId: null, runId: "r" });
  });

  test("missing params are null", () => {
    assert.deepEqual(readAddQuery(""), { familyId: null, memoryId: null, runId: null });
    assert.deepEqual(readAddQuery("?other=1"), { familyId: null, memoryId: null, runId: null });
    assert.deepEqual(readAddQuery(null), { familyId: null, memoryId: null, runId: null });
  });

  test("empty values are null", () => {
    assert.deepEqual(readAddQuery("?family=&memoryId=&run="), {
      familyId: null,
      memoryId: null,
      runId: null,
    });
    assert.deepEqual(readAddQuery("?family&run"), { familyId: null, memoryId: null, runId: null });
  });

  test("takes a URLSearchParams and decodes values", () => {
    const params = new URLSearchParams();
    params.set("family", "fam a/b");
    params.set("memoryId", "m&1");
    assert.deepEqual(readAddQuery(params), { familyId: "fam a/b", memoryId: "m&1", runId: null });
  });

  test("the first of repeated params wins", () => {
    assert.equal(readAddQuery("?run=a&run=b").runId, "a");
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
    assert.deepEqual(q, { familyId: "a&b=c", memoryId: null, runId: "r/1 ?#" });
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
    assert.deepEqual(m, { title: "The hymn books", year: 1988, participantIds: ["dad", "mom"] });
    m.participantIds.push("x");
    assert.deepEqual(ids, ["dad", "mom"]);
  });

  test("no participants listed", () => {
    const res = { memory: { id: "m", familyId: "f", title: "T", year: 1970 } };
    assert.deepEqual(momentFromMemory(res), { title: "T", year: 1970, participantIds: [] });
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
