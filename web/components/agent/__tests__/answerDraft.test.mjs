// Run with: cd web && node --test "components/agent/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in answerDraft.ts.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  ANSWER_DRAFT_KEY,
  MAX_DRAFT_RUNS,
  clearAnswerDraft,
  draftPauseKey,
  readAnswerDraft,
  saveAnswerDraft,
  sessionDraftStore,
} from "../answerDraft.ts";

/** A Storage-like map, recording each call. */
function memoryStorage() {
  const map = new Map();
  const calls = [];
  return {
    calls,
    map,
    get length() {
      return map.size;
    },
    key: (i) => [...map.keys()][i] ?? null,
    getItem(k) {
      calls.push(["get", k]);
      return map.has(k) ? map.get(k) : null;
    },
    setItem(k, v) {
      calls.push(["set", k]);
      map.set(k, String(v));
    },
    removeItem(k) {
      calls.push(["remove", k]);
      map.delete(k);
    },
    clear() {
      map.clear();
    },
  };
}

/** Storage whose every call throws, as a blocked or full sessionStorage does. */
function throwingStorage() {
  const boom = () => {
    throw new DOMException("blocked", "SecurityError");
  };
  return { getItem: boom, setItem: boom, removeItem: boom, clear: boom, key: boom, length: 0 };
}

const RUN = "fam_1/run_1";
const PAUSE = draftPauseKey("answer", 4);

test("the storage key and pause key", () => {
  assert.equal(ANSWER_DRAFT_KEY, "stitch.answerDraft");
  assert.equal(MAX_DRAFT_RUNS, 10);
  assert.equal(draftPauseKey("answer", 4), "answer-4");
});

test("save then read gives the text back, under the one key", () => {
  const s = memoryStorage();
  saveAnswerDraft(s, RUN, PAUSE, "My cousin held the umbrella");
  assert.equal(readAnswerDraft(s, RUN, PAUSE), "My cousin held the umbrella");
  assert.deepEqual([...s.map.keys()], [ANSWER_DRAFT_KEY]);
  // Whitespace and line breaks are kept as typed.
  saveAnswerDraft(s, RUN, PAUSE, "  two\nlines ");
  assert.equal(readAnswerDraft(s, RUN, PAUSE), "  two\nlines ");
});

test("read is '' when there is no draft", () => {
  const s = memoryStorage();
  assert.equal(readAnswerDraft(s, RUN, PAUSE), "");
  saveAnswerDraft(s, RUN, PAUSE, "x");
  assert.equal(readAnswerDraft(s, RUN, "answer-9"), "", "another pause");
  assert.equal(readAnswerDraft(s, "fam_1/run_2", PAUSE), "", "another run");
  assert.equal(readAnswerDraft(null, RUN, PAUSE), "");
  assert.equal(readAnswerDraft(undefined, RUN, PAUSE), "");
});

test("an empty text removes the entry, and the key once nothing is left", () => {
  const s = memoryStorage();
  saveAnswerDraft(s, RUN, PAUSE, "draft");
  saveAnswerDraft(s, RUN, "answer-9", "other");
  saveAnswerDraft(s, RUN, PAUSE, "");
  assert.equal(readAnswerDraft(s, RUN, PAUSE), "");
  assert.equal(readAnswerDraft(s, RUN, "answer-9"), "other", "the run's other pause stays");
  saveAnswerDraft(s, RUN, "answer-9", "");
  assert.equal(s.map.has(ANSWER_DRAFT_KEY), false);
  // Removing what was never there writes nothing.
  s.calls.length = 0;
  saveAnswerDraft(s, RUN, PAUSE, "");
  assert.deepEqual(
    s.calls.filter(([op]) => op !== "get"),
    [],
  );
});

test("clear forgets one run, or every run", () => {
  const s = memoryStorage();
  saveAnswerDraft(s, RUN, PAUSE, "one");
  saveAnswerDraft(s, RUN, "answer-9", "two");
  saveAnswerDraft(s, "fam_1/run_2", PAUSE, "three");
  clearAnswerDraft(s, RUN);
  assert.equal(readAnswerDraft(s, RUN, PAUSE), "");
  assert.equal(readAnswerDraft(s, RUN, "answer-9"), "");
  assert.equal(readAnswerDraft(s, "fam_1/run_2", PAUSE), "three");
  clearAnswerDraft(s, "never/saved");
  assert.equal(readAnswerDraft(s, "fam_1/run_2", PAUSE), "three");
  clearAnswerDraft(s);
  assert.equal(s.map.has(ANSWER_DRAFT_KEY), false);
  clearAnswerDraft(null);
  clearAnswerDraft(undefined, RUN);
});

test("at most 10 runs are kept; the run saved longest ago goes first", () => {
  const s = memoryStorage();
  for (let i = 0; i < 12; i++) saveAnswerDraft(s, `f/run_${i}`, PAUSE, `text ${i}`);
  assert.equal(readAnswerDraft(s, "f/run_0", PAUSE), "");
  assert.equal(readAnswerDraft(s, "f/run_1", PAUSE), "");
  for (let i = 2; i < 12; i++) assert.equal(readAnswerDraft(s, `f/run_${i}`, PAUSE), `text ${i}`);
  assert.equal(JSON.parse(s.map.get(ANSWER_DRAFT_KEY)).length, MAX_DRAFT_RUNS);

  // Typing again in an old run makes it the most recent, so it outlives newer ones.
  saveAnswerDraft(s, "f/run_2", PAUSE, "still typing");
  saveAnswerDraft(s, "f/run_12", PAUSE, "new");
  assert.equal(readAnswerDraft(s, "f/run_2", PAUSE), "still typing");
  assert.equal(readAnswerDraft(s, "f/run_3", PAUSE), "", "now the oldest, so dropped");
  assert.equal(readAnswerDraft(s, "f/run_12", PAUSE), "new");
});

test("storage that throws never throws out of the helpers", () => {
  const s = throwingStorage();
  assert.doesNotThrow(() => saveAnswerDraft(s, RUN, PAUSE, "x"));
  assert.doesNotThrow(() => saveAnswerDraft(s, RUN, PAUSE, ""));
  assert.equal(readAnswerDraft(s, RUN, PAUSE), "");
  assert.doesNotThrow(() => clearAnswerDraft(s, RUN));
  assert.doesNotThrow(() => clearAnswerDraft(s));

  // Reading works but writing is full (QuotaExceededError): the draft just isn't kept.
  const full = memoryStorage();
  full.setItem = () => {
    throw new DOMException("full", "QuotaExceededError");
  };
  assert.doesNotThrow(() => saveAnswerDraft(full, RUN, PAUSE, "x"));
  assert.equal(readAnswerDraft(full, RUN, PAUSE), "");
});

test("junk under the key reads as no drafts, and the next save replaces it", () => {
  for (const junk of ["not json", "{}", "null", '[["r"]]', '[[1, {"a": "b"}]]', '[["r", ["x"]]]', '[["r", {"p": 3}]]']) {
    const s = memoryStorage();
    s.map.set(ANSWER_DRAFT_KEY, junk);
    assert.equal(readAnswerDraft(s, "r", "p"), "", junk);
    saveAnswerDraft(s, RUN, PAUSE, "fresh");
    assert.equal(readAnswerDraft(s, RUN, PAUSE), "fresh", junk);
  }
  // Good runs next to a bad one survive.
  const s = memoryStorage();
  s.map.set(ANSWER_DRAFT_KEY, JSON.stringify([["bad"], [RUN, { [PAUSE]: "kept", other: 5 }]]));
  assert.equal(readAnswerDraft(s, RUN, PAUSE), "kept");
});

test("sessionDraftStore is null outside a browser", () => {
  assert.equal(typeof window, "undefined");
  assert.equal(sessionDraftStore(), null);
});
