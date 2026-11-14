// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in memoryRunState.ts and runState.ts. States
// are built with the real run reducer, so the keys are checked against the
// sequence numbers it actually hands out.
import { test } from "node:test";
import assert from "node:assert/strict";

import { initialRunState, runReducer } from "../../agent/runState.ts";
import { addAnotherHrefFor, pendingCard, pendingKey, sceneHrefFor } from "../memoryRunState.ts";

const ev = (event, data) => ({ type: "event", message: { event, data } });
const answered = (kind, summary = "ok") => ({ type: "answered", kind, summary });
const run = (...actions) => actions.reduce(runReducer, initialRunState);

const QUESTION = ev("question", { text: "Who else was in the kitchen?" });
const PROPS = ev("props_found", {
  props: [{ id: "c1", name: "hymn book", size: "hand" }],
});
const MERGE = ev("merge", { text: "Is Grandma Rose the same person as Rose?", personId: "p_rose", candidateName: "Rose" });

const PHASES = ["connecting", "running", "waiting", "scene_ready", "done", "error"];
const PENDINGS = {
  none: null,
  answer: { kind: "answer", question: { text: "Where was this?" } },
  props: { kind: "props", props: [] },
  merge: { kind: "merge", merge: { text: "Same person?", personId: "p1", candidateName: "Ann" } },
};

test("pendingCard: every phase and pending combination", () => {
  for (const phase of PHASES) {
    for (const [name, pending] of Object.entries(PENDINGS)) {
      const expected = phase === "waiting" && pending ? pending.kind : null;
      assert.equal(pendingCard({ phase, pending }), expected, `${phase} / ${name}`);
    }
  }
});

test("pendingCard: follows the reducer through a run", () => {
  assert.equal(pendingCard(initialRunState), null);
  assert.equal(pendingCard(run(ev("plan", { todos: [] }))), null);
  assert.equal(pendingCard(run(QUESTION)), "answer");
  assert.equal(pendingCard(run(PROPS)), "props");
  assert.equal(pendingCard(run(MERGE)), "merge");
  assert.equal(pendingCard(run(QUESTION, answered("answer"))), null);
  // A drop mid-pause keeps the pending input but the run is not listening.
  const dropped = run(QUESTION, { type: "connection_error", message: "Lost the studio." });
  assert.notEqual(dropped.pending, null);
  assert.equal(pendingCard(dropped), null);
  assert.equal(pendingCard(run(MERGE, ev("done", { memoryId: "m1" }))), null);
});

test("pendingKey: two consecutive follow-ups with the same text get different keys", () => {
  const first = run(ev("plan", { todos: [] }), QUESTION);
  const second = runReducer(runReducer(first, answered("answer", "the aunts")), QUESTION);
  assert.equal(pendingCard(first), "answer");
  assert.equal(pendingCard(second), "answer");
  assert.equal(first.pending.question.text, second.pending.question.text);
  assert.notEqual(pendingKey(first), pendingKey(second));
});

test("pendingKey: two different follow-ups in a row get different keys", () => {
  const first = run(QUESTION);
  const second = runReducer(
    runReducer(first, answered("answer")),
    ev("question", { text: "What year was it?" }),
  );
  assert.notEqual(pendingKey(first), pendingKey(second));
});

test("pendingKey: stable across re-renders of the same pause", () => {
  const paused = run(ev("subagent_start", { name: "interviewer" }), QUESTION);
  const key = pendingKey(paused);
  assert.equal(pendingKey(paused), key);
  assert.equal(pendingKey({ ...paused }), key);
  assert.equal(pendingKey({ pending: paused.pending, seq: paused.seq }), key);
  // A replay of the same events (reconnect with replay) lands on the same key.
  assert.equal(pendingKey(run(ev("subagent_start", { name: "interviewer" }), QUESTION)), key);
});

test("pendingKey: differs by kind and is 'none' without a pause", () => {
  assert.equal(pendingKey(initialRunState), "none");
  const keys = [run(QUESTION), run(PROPS), run(MERGE)].map(pendingKey);
  assert.equal(new Set(keys).size, 3);
  assert.match(keys[0], /^answer-/);
  assert.match(keys[1], /^props-/);
  assert.match(keys[2], /^merge-/);
  // Questions, props and merge one after another never collide.
  const s1 = run(QUESTION);
  const s2 = runReducer(runReducer(s1, answered("answer")), PROPS);
  const s3 = runReducer(runReducer(s2, answered("props")), MERGE);
  assert.equal(new Set([s1, s2, s3].map(pendingKey)).size, 3);
});

test("sceneHrefFor: only once the run has named its memory, and only with a builder", () => {
  const href = (id) => `/memories/${id}`;
  assert.equal(sceneHrefFor(initialRunState, href), undefined);
  assert.equal(sceneHrefFor(run(QUESTION), href), undefined);
  const ready = run(ev("scene_ready", { memoryId: "m_42", version: 1 }));
  assert.equal(sceneHrefFor(ready, href), "/memories/m_42");
  assert.equal(sceneHrefFor(ready, undefined), undefined);
  assert.equal(sceneHrefFor(run(ev("done", { memoryId: "m_7" })), href), "/memories/m_7");
});

test("addAnotherHrefFor: only when done and given somewhere to go", () => {
  for (const phase of PHASES) {
    assert.equal(addAnotherHrefFor({ phase }, "/add"), phase === "done" ? "/add" : undefined, phase);
    assert.equal(addAnotherHrefFor({ phase }, undefined), undefined, phase);
    assert.equal(addAnotherHrefFor({ phase }, ""), undefined, phase);
  }
});
