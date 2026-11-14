// Run with: cd web && node --test "components/agent/__tests__/*.test.mjs"
// (Node 22+ treats each argument as a file or glob and does not walk a directory.)
// Node strips the erasable TypeScript in runState.ts; no build step needed.
import { test } from "node:test";
import assert from "node:assert/strict";

import { initialRunState, needsInput, runReducer } from "../runState.ts";

const ev = (event, data) => ({ type: "event", message: { event, data } });

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const v of Object.values(value)) deepFreeze(v);
  }
  return value;
}

/** Folds actions through the reducer, freezing every intermediate state. */
function run(...actions) {
  return actions.reduce((s, a) => deepFreeze(runReducer(s, deepFreeze(a))), deepFreeze(initialRunState));
}

test("initial state is connecting and empty", () => {
  assert.equal(initialRunState.phase, "connecting");
  assert.deepEqual(initialRunState.todos, []);
  assert.deepEqual(initialRunState.steps, []);
  assert.deepEqual(initialRunState.log, []);
  assert.equal(initialRunState.pending, null);
  assert.equal(initialRunState.memoryId, null);
  assert.equal(initialRunState.version, null);
  assert.equal(initialRunState.error, null);
  assert.equal(initialRunState.seq, 0);
  assert.equal(needsInput(initialRunState), false);
});

test("plan replaces todos and starts the run", () => {
  const s = run(
    ev("plan", { todos: [{ text: "Listen", status: "in_progress" }, { text: "Sketch" }] }),
    ev("plan", { todos: [{ text: "Listen", status: "completed" }, { text: "Sketch", status: "in_progress" }] }),
  );
  assert.equal(s.phase, "running");
  assert.deepEqual(s.todos, [
    { text: "Listen", status: "completed" },
    { text: "Sketch", status: "in_progress" },
  ]);
  assert.equal(s.log.length, 0, "plan does not log");
});

test("subagent_start appends a running step and logs it", () => {
  const s = run(ev("subagent_start", { name: "interviewer", task: "Read the story" }));
  assert.equal(s.phase, "running");
  assert.equal(s.steps.length, 1);
  assert.deepEqual(
    { name: s.steps[0].name, task: s.steps[0].task, status: s.steps[0].status },
    { name: "interviewer", task: "Read the story", status: "running" },
  );
  assert.deepEqual(s.log, [{ seq: 1, kind: "step", stepKey: s.steps[0].key }]);
  assert.equal(s.seq, 1);
});

test("subagent_end closes the matching running step, keeping the task", () => {
  const s = run(
    ev("subagent_start", { name: "interviewer", task: "Read the story" }),
    ev("subagent_start", { name: "storyboarder" }),
    ev("subagent_end", { name: "interviewer", summary: "Enough detail to stage" }),
  );
  assert.equal(s.steps[0].status, "done");
  assert.equal(s.steps[0].summary, "Enough detail to stage");
  assert.equal(s.steps[0].task, "Read the story");
  assert.equal(s.steps[1].status, "running");
  assert.equal(s.log.length, 2, "end of a known step does not add a log entry");
});

test("repeated director steps pair with the latest running one", () => {
  const s = run(
    ev("subagent_start", { name: "director", task: "First draft" }),
    ev("subagent_end", { name: "director", summary: "Draft 1" }),
    ev("subagent_start", { name: "director", task: "Fix round 1" }),
    ev("subagent_start", { name: "director", task: "Nested" }),
    ev("subagent_end", { name: "director", summary: "Nested done" }),
  );
  assert.equal(s.steps.length, 3);
  assert.deepEqual(
    s.steps.map((x) => [x.task, x.status, x.summary]),
    [
      ["First draft", "done", "Draft 1"],
      ["Fix round 1", "running", null],
      ["Nested", "done", "Nested done"],
    ],
  );
  const keys = new Set(s.steps.map((x) => x.key));
  assert.equal(keys.size, 3, "step keys are unique");
  assert.deepEqual(
    s.log.map((e) => e.stepKey),
    s.steps.map((x) => x.key),
  );
});

test("subagent_end without a running step appends a done step", () => {
  const s = run(ev("subagent_end", { name: "checker", summary: "Looks right" }));
  assert.equal(s.steps.length, 1);
  assert.equal(s.steps[0].status, "done");
  assert.equal(s.steps[0].summary, "Looks right");
  assert.equal(s.log[0].kind, "step");
  assert.equal(s.log[0].stepKey, s.steps[0].key);
});

test("question pauses for an answer, answered resumes", () => {
  let s = run(ev("subagent_start", { name: "interviewer" }), ev("question", { text: "Where was it?" }));
  assert.equal(s.phase, "waiting");
  assert.deepEqual(s.pending, { kind: "answer", question: { text: "Where was it?" } });
  assert.equal(needsInput(s), true);
  assert.deepEqual(s.log.at(-1), { seq: 2, kind: "asked", text: "Where was it?" });

  s = deepFreeze(runReducer(s, { type: "answered", kind: "answer", summary: "At the lake" }));
  assert.equal(s.phase, "running");
  assert.equal(s.pending, null);
  assert.equal(needsInput(s), false);
  assert.deepEqual(s.log.at(-1), {
    seq: 3,
    kind: "answered",
    answer: "answer",
    summary: "At the lake",
    prompt: "Where was it?",
  });
});

test("props_found pauses for review, answered resumes", () => {
  const props = [
    { id: "p1", name: "hymn books", size: "hand" },
    { id: "p2", name: "station wagon", size: "vehicle", reused: true },
  ];
  let s = run(ev("props_found", { props }));
  assert.equal(s.phase, "waiting");
  assert.deepEqual(s.pending, { kind: "props", props });
  assert.equal(needsInput(s), true);
  assert.deepEqual(s.log.at(-1), { seq: 1, kind: "props", count: 2 });

  s = deepFreeze(runReducer(s, { type: "answered", kind: "props", summary: "Kept 2 props" }));
  assert.equal(s.phase, "running");
  assert.equal(s.pending, null);
  assert.deepEqual(s.log.at(-1), { seq: 2, kind: "answered", answer: "props", summary: "Kept 2 props", prompt: null });
});

test("merge pauses for approval, answered resumes", () => {
  const merge = { text: "Is Uncle Joe the Joseph in the tree?", personId: "joseph", candidateName: "Uncle Joe" };
  let s = run(ev("merge", merge));
  assert.equal(s.phase, "waiting");
  assert.deepEqual(s.pending, { kind: "merge", merge });
  assert.equal(needsInput(s), true);
  assert.deepEqual(s.log.at(-1), { seq: 1, kind: "merge", text: merge.text });

  s = deepFreeze(runReducer(s, { type: "answered", kind: "merge", summary: "Yes, same person" }));
  assert.equal(s.phase, "running");
  assert.equal(s.pending, null);
  assert.deepEqual(s.log.at(-1), {
    seq: 2,
    kind: "answered",
    answer: "merge",
    summary: "Yes, same person",
    prompt: merge.text,
  });
});

test("answered with no pending input or the wrong kind changes nothing", () => {
  const running = run(ev("plan", { todos: [] }));
  assert.equal(runReducer(running, { type: "answered", kind: "answer", summary: "x" }), running);

  const waiting = run(ev("question", { text: "Who?" }));
  assert.equal(runReducer(waiting, { type: "answered", kind: "props", summary: "x" }), waiting);
});

test("informational events while waiting keep the run paused", () => {
  const s = run(ev("question", { text: "Who?" }), ev("plan", { todos: [{ text: "Wait" }] }));
  assert.equal(s.phase, "waiting");
  assert.equal(needsInput(s), true);
});

test("compile_error and frames are logged in order", () => {
  const compile = {
    round: 1,
    errors: [
      { code: "not_holding", message: 'shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it' },
      { code: "unknown_mark", message: "shot s2: unknown mark porch" },
    ],
  };
  const frames = { frames: [{ t: 1.5, mediaId: "m1" }, { t: 4, mediaId: "m2" }], critique: "Dad is hidden" };
  const s = run(ev("compile_error", compile), ev("frames", frames));
  assert.equal(s.phase, "running");
  assert.deepEqual(s.log, [
    { seq: 1, kind: "compile_error", event: compile },
    { seq: 2, kind: "frames", event: frames },
  ]);
});

test("scene_ready then done", () => {
  let s = run(ev("scene_ready", { memoryId: "mem1", version: 2 }));
  assert.equal(s.phase, "scene_ready");
  assert.equal(s.memoryId, "mem1");
  assert.equal(s.version, 2);
  assert.deepEqual(s.log.at(-1), { seq: 1, kind: "scene_ready", memoryId: "mem1" });

  // The linker keeps working after the scene is ready; the phase stays put.
  s = deepFreeze(runReducer(s, ev("subagent_start", { name: "linker" })));
  assert.equal(s.phase, "scene_ready");

  s = deepFreeze(runReducer(s, ev("done", { memoryId: "mem1" })));
  assert.equal(s.phase, "done");
  assert.equal(s.memoryId, "mem1");
  assert.equal(s.version, 2);
});

test("scene_ready without a version sets version null", () => {
  const s = run(ev("scene_ready", { memoryId: "mem1" }));
  assert.equal(s.version, null);
});

test("error event sets the phase and message", () => {
  const s = run(ev("plan", { todos: [] }), ev("error", { message: "The director gave up" }));
  assert.equal(s.phase, "error");
  assert.equal(s.error, "The director gave up");
  assert.deepEqual(s.log.at(-1), { seq: 1, kind: "error", message: "The director gave up" });
});

test("connection_error before done sets the error", () => {
  const s = run(ev("plan", { todos: [] }), { type: "connection_error", message: "Lost connection" });
  assert.equal(s.phase, "error");
  assert.equal(s.error, "Lost connection");
});

test("connection_error after done is ignored", () => {
  const done = run(ev("done", { memoryId: "mem1" }));
  const after = runReducer(done, { type: "connection_error", message: "Stream closed" });
  assert.equal(after, done);
  assert.equal(after.phase, "done");
  assert.equal(after.error, null);
});

test("events after done are ignored", () => {
  const done = run(ev("plan", { todos: [{ text: "A" }] }), ev("done", { memoryId: "mem1" }));
  for (const action of [
    ev("plan", { todos: [] }),
    ev("subagent_start", { name: "director" }),
    ev("question", { text: "Late?" }),
    ev("error", { message: "late" }),
    ev("scene_ready", { memoryId: "other" }),
    ev("done", { memoryId: "other" }),
  ]) {
    assert.equal(runReducer(done, action), done);
  }
});

test("reset returns the initial state, even after done", () => {
  const done = run(ev("plan", { todos: [{ text: "A" }] }), ev("done", { memoryId: "mem1" }));
  assert.equal(runReducer(done, { type: "reset" }), initialRunState);
});

test("the reducer never mutates its input", () => {
  // run() deep-freezes every state and action, so any mutation would throw in strict mode.
  const before = run(
    ev("plan", { todos: [{ text: "A", status: "in_progress" }] }),
    ev("subagent_start", { name: "director" }),
  );
  const snapshot = structuredClone(before);
  const after = run(
    ev("plan", { todos: [{ text: "A", status: "in_progress" }] }),
    ev("subagent_start", { name: "director" }),
    ev("subagent_end", { name: "director", summary: "ok" }),
    ev("props_found", { props: [{ id: "p", name: "cap", size: "hand" }] }),
    { type: "answered", kind: "props", summary: "kept" },
    ev("compile_error", { errors: [], round: 2 }),
    ev("scene_ready", { memoryId: "m" }),
    ev("error", { message: "x" }),
    { type: "connection_error", message: "y" },
    ev("done", { memoryId: "m" }),
  );
  assert.deepEqual(before, snapshot);
  assert.notEqual(after, before);

  // Direct check: the previous state object is untouched after a step change.
  const next = runReducer(before, ev("subagent_end", { name: "director" }));
  assert.equal(before.steps[0].status, "running");
  assert.equal(next.steps[0].status, "done");
  assert.notEqual(next.steps, before.steps);
});

test("unknown events are ignored", () => {
  const s = run(ev("plan", { todos: [] }));
  assert.equal(runReducer(s, ev("heartbeat", {})), s);
});

test("waking from an error clears the stale message", () => {
  const s = run(ev("error", { message: "boom" }), ev("plan", { todos: [] }));
  assert.equal(s.phase, "running");
  assert.equal(s.error, null);
  const t = run(ev("error", { message: "boom" }), ev("subagent_start", { name: "director" }));
  assert.equal(t.phase, "running");
  assert.equal(t.error, null);
});

test("answering after an error while waiting clears the error", () => {
  const s = run(
    ev("question", { text: "Who was there?" }),
    ev("error", { message: "boom" }),
    { type: "answered", kind: "answer", summary: "Grandma" },
  );
  assert.equal(s.phase, "running");
  assert.equal(s.pending, null);
  assert.equal(s.error, null);
});

// ---------------------------------------------------------------------------
// A pause answered before a reload: the replayed run has no record of the
// answer, so the events after the pause have to close it.

const answeredLines = (s) => s.log.filter((e) => e.kind === "answered");

test("reviewer's repro: a new sub-agent after a replayed question closes it", () => {
  const s = run(
    ev("plan", { todos: [{ text: "Listen", status: "in_progress" }] }),
    ev("subagent_start", { name: "interviewer" }),
    ev("subagent_end", { name: "interviewer", summary: "One question" }),
    ev("question", { text: "Who drove?" }),
    ev("plan", { todos: [{ text: "Listen", status: "completed" }, { text: "Props", status: "in_progress" }] }),
    ev("subagent_start", { name: "prop_master" }),
  );
  assert.equal(s.phase, "running");
  assert.equal(s.pending, null);
  assert.equal(needsInput(s), false);
  assert.equal(s.pauseSteps, null);
  assert.equal(s.pauseSeq, null);
});

test("the quiet line is neutral and comes before the step that showed the run moved on", () => {
  const s = run(ev("question", { text: "Who drove?" }), ev("subagent_start", { name: "storyboarder" }));
  assert.deepEqual(
    s.log.map((e) => e.kind),
    ["asked", "answered", "step"],
  );
  assert.deepEqual(answeredLines(s), [
    { seq: 2, kind: "answered", answer: "answer", summary: null, prompt: "Who drove?" },
  ]);
});

test("a pause records the steps running at that moment", () => {
  const s = run(
    ev("subagent_start", { name: "storyboarder" }),
    ev("subagent_end", { name: "storyboarder" }),
    ev("subagent_start", { name: "prop_master" }),
    ev("props_found", { props: [{ id: "p", name: "cap", size: "hand" }] }),
  );
  const pm = s.steps.find((x) => x.name === "prop_master");
  assert.deepEqual(s.pauseSteps, [pm.key]);
  assert.equal(s.pauseSeq, s.seq, "pauseSeq is the pause's own entry");
});

test("the paused step ending closes a props review; the next step starts on a running feed", () => {
  const s = run(
    ev("subagent_start", { name: "prop_master" }),
    ev("props_found", { props: [{ id: "p", name: "cap", size: "hand" }] }),
    ev("subagent_end", { name: "prop_master", summary: "Kept 1 prop." }),
    ev("subagent_start", { name: "director" }),
  );
  assert.equal(s.pending, null);
  assert.equal(s.phase, "running");
  assert.equal(answeredLines(s).length, 1, "closed once, logged once");
  assert.equal(answeredLines(s)[0].answer, "props");
  // Already closed by the end alone.
  const ended = run(
    ev("subagent_start", { name: "prop_master" }),
    ev("props_found", { props: [] }),
    ev("subagent_end", { name: "prop_master", summary: "Kept 0 props." }),
  );
  assert.equal(ended.pending, null);
  assert.equal(ended.phase, "running");
});

test("with two branches running, the pause waits for both to end", () => {
  let s = run(
    ev("subagent_start", { name: "a" }),
    ev("subagent_start", { name: "b" }),
    ev("question", { text: "Which church?" }),
    ev("subagent_end", { name: "b" }),
  );
  assert.equal(s.phase, "waiting");
  assert.deepEqual(s.pending, { kind: "answer", question: { text: "Which church?" } });
  assert.equal(needsInput(s), true);

  s = deepFreeze(runReducer(s, ev("subagent_end", { name: "a" })));
  assert.equal(s.phase, "running");
  assert.equal(s.pending, null);
  assert.equal(answeredLines(s).length, 1);
});

test("plan, frames and compile_error never close a pause", () => {
  const q = run(ev("question", { text: "Who?" }));
  const s = run(
    ev("question", { text: "Who?" }),
    ev("plan", { todos: [{ text: "Wait" }] }),
    ev("frames", { frames: [{ t: 1, mediaId: "m1" }] }),
    ev("compile_error", { errors: [{ code: "x", message: "y" }], round: 1 }),
  );
  assert.equal(s.phase, "waiting");
  assert.deepEqual(s.pending, q.pending);
  assert.equal(s.pauseSeq, q.pauseSeq);
  assert.equal(answeredLines(s).length, 0);
});

test("scene_ready and done close a pause", () => {
  const ready = run(ev("question", { text: "Who?" }), ev("scene_ready", { memoryId: "m1" }));
  assert.equal(ready.pending, null);
  assert.equal(ready.phase, "scene_ready");
  assert.deepEqual(
    ready.log.map((e) => e.kind),
    ["asked", "answered", "scene_ready"],
  );

  const done = run(ev("merge", { text: "Same?", personId: "p", candidateName: "Joe" }), ev("done", { memoryId: "m1" }));
  assert.equal(done.pending, null);
  assert.equal(done.phase, "done");
  assert.equal(answeredLines(done).length, 1);
});

test("a new pause replaces the last and logs it as answered", () => {
  const s = run(
    ev("question", { text: "Who?" }),
    ev("merge", { text: "Same?", personId: "p", candidateName: "Joe" }),
  );
  assert.equal(s.phase, "waiting");
  assert.equal(s.pending.kind, "merge");
  assert.deepEqual(
    s.log.map((e) => e.kind),
    ["asked", "answered", "merge"],
  );
  assert.equal(s.pauseSeq, 3);
});

test("the family's answer after the run already moved on fills the neutral line in", () => {
  // The answer POST resolved after the next sub-agent had started.
  const s = run(
    ev("question", { text: "Who drove?" }),
    ev("subagent_start", { name: "storyboarder" }),
    { type: "answered", kind: "answer", summary: "Grandpa" },
  );
  assert.deepEqual(answeredLines(s).map((e) => e.summary), ["Grandpa"]);
  assert.equal(s.log.length, 3, "nothing new logged");
  // A second fill, or one of another kind, changes nothing.
  assert.equal(runReducer(s, { type: "answered", kind: "answer", summary: "Again" }), s);
  const other = run(ev("question", { text: "Who?" }), ev("subagent_start", { name: "x" }));
  assert.equal(runReducer(other, { type: "answered", kind: "props", summary: "Kept 1 prop" }), other);
});

test("a neutral answered action closes the pause with a neutral line", () => {
  const s = run(ev("question", { text: "Who?" }), { type: "answered", kind: "answer", summary: null });
  assert.equal(s.pending, null);
  assert.equal(s.phase, "running");
  assert.deepEqual(answeredLines(s).map((e) => e.summary), [null]);
  // Nothing to close: no change.
  const idle = run(ev("plan", { todos: [] }));
  assert.equal(runReducer(idle, { type: "answered", kind: "answer", summary: null }), idle);
});

test("steps note where they ended", () => {
  const s = run(
    ev("subagent_start", { name: "prop_master" }),
    ev("props_found", { props: [] }),
    { type: "answered", kind: "props", summary: "No changes" },
    ev("subagent_end", { name: "prop_master", summary: "Kept 0 props." }),
  );
  assert.equal(s.steps[0].endSeq, 3);
  const lone = run(ev("subagent_end", { name: "checker" }));
  assert.equal(lone.steps[0].endSeq, lone.log[0].seq);
});

// ---------------------------------------------------------------------------
// errorFrom: where an error came from (the run itself, or the connection)

test("initial state has no error origin", () => {
  assert.equal(initialRunState.errorFrom, null);
});

test("regression: the run's own error event marks the error as the run's", () => {
  const s = run(ev("plan", { todos: [{ text: "Listen", status: "in_progress" }] }), ev("error", { message: "The studio stopped." }));
  assert.equal(s.phase, "error");
  assert.equal(s.error, "The studio stopped.");
  assert.equal(s.errorFrom, "run");
});

test("a connection that gives up marks the error as the connection's", () => {
  const s = run(ev("plan", { todos: [] }), { type: "connection_error", message: "Lost connection" });
  assert.equal(s.phase, "error");
  assert.equal(s.errorFrom, "connection");
});

test("an informational event after an error wakes the run and clears where the error came from", () => {
  for (const wakeEvent of [
    ev("plan", { todos: [] }),
    ev("subagent_start", { name: "director" }),
    ev("subagent_end", { name: "director" }),
    ev("compile_error", { errors: [], round: 1 }),
    ev("frames", { frames: [] }),
  ]) {
    const fromRun = run(ev("error", { message: "boom" }), wakeEvent);
    assert.equal(fromRun.phase, "running", wakeEvent.message.event);
    assert.equal(fromRun.error, null, wakeEvent.message.event);
    assert.equal(fromRun.errorFrom, null, wakeEvent.message.event);
    const fromConnection = run({ type: "connection_error", message: "lost" }, wakeEvent);
    assert.equal(fromConnection.errorFrom, null, wakeEvent.message.event);
  }
});

test("a new pause after an error clears where it came from", () => {
  const s = run(ev("error", { message: "boom" }), ev("question", { text: "Who?" }));
  assert.equal(s.phase, "waiting");
  assert.equal(s.error, null);
  assert.equal(s.errorFrom, null);
});

test("answering a pause after an error clears where it came from", () => {
  const s = run(
    ev("question", { text: "Who?" }),
    { type: "connection_error", message: "lost" },
    { type: "answered", kind: "answer", summary: "Grandpa" },
  );
  assert.equal(s.phase, "running");
  assert.equal(s.error, null);
  assert.equal(s.errorFrom, null);
});

test("done and reset leave no error origin", () => {
  const done = run(ev("error", { message: "boom" }), ev("done", { memoryId: "m1" }));
  assert.equal(done.phase, "done");
  assert.equal(done.errorFrom, null);
  const reset = runReducer(run(ev("error", { message: "boom" })), { type: "reset" });
  assert.equal(reset, initialRunState);
  assert.equal(reset.errorFrom, null);
  const resetConn = runReducer(run({ type: "connection_error", message: "lost" }), { type: "reset" });
  assert.equal(resetConn.errorFrom, null);
});

test("a later error keeps the latest origin", () => {
  const s = run(ev("error", { message: "boom" }), { type: "connection_error", message: "lost" });
  assert.equal(s.errorFrom, "connection");
  const t = run({ type: "connection_error", message: "lost" }, ev("error", { message: "boom" }));
  assert.equal(t.errorFrom, "run");
});
