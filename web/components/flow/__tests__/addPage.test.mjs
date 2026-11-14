// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in addPage.ts; it has type-only imports,
// so no resolve hook is needed.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  DRAFT_KEY_PREFIX,
  RUN_GONE_MESSAGE,
  TELLING_KEY_PREFIX,
  draftFromSent,
  familyChoice,
  loadSentDraft,
  loadTelling,
  momentFromMemory,
  questionForMemory,
  readAddQuery,
  restoreNotice,
  retryHref,
  runHref,
  saveSentDraft,
  saveTelling,
  sentDraftFromSubmit,
  tellingFromSubmit,
  withRunGone,
} from "../addPage.ts";
import { pickFamily } from "../session.ts";

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
