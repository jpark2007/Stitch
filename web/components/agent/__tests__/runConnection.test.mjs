// Run with: cd web && node --test "components/agent/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in runConnection.ts and runState.ts.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_MAX_RETRIES,
  HEALTHY_CONNECTION_MS,
  answerBody,
  answerSummary,
  connectionErrorMessage,
  connectionMadeProgress,
  defaultRetryDelay,
  followRun,
  isFinalEvent,
  isFinished,
  isNotWaiting,
  MAX_SAVED_ANSWERS,
  answersStorageKey,
  newRunBook,
  nextConnectionStep,
  parseAnswers,
  recordAnswer,
  sendAnswer,
  serializeAnswers,
} from "../runConnection.ts";
import { initialRunState, runReducer } from "../runState.ts";

const msg = (event, data) => ({ event, data });
const PLAN = msg("plan", { todos: [{ text: "Listen", status: "in_progress" }] });
const START = msg("subagent_start", { name: "interviewer", task: "Read the story" });
const END = msg("subagent_end", { name: "interviewer", summary: "Heard it" });
const QUESTION = msg("question", { text: "Who drove?" });
const DIRECTOR = msg("subagent_start", { name: "director" });
const DONE = msg("done", { memoryId: "m1" });
const ERROR = msg("error", { message: "The studio stopped." });

// ---------------------------------------------------------------------------
// defaultRetryDelay

test("defaultRetryDelay doubles from 1s and caps at 8s", () => {
  assert.equal(defaultRetryDelay(1), 1000);
  assert.equal(defaultRetryDelay(2), 2000);
  assert.equal(defaultRetryDelay(3), 4000);
  assert.equal(defaultRetryDelay(4), 8000);
  assert.equal(defaultRetryDelay(5), 8000);
  assert.equal(defaultRetryDelay(12), 8000);
  assert.equal(defaultRetryDelay(5000), 8000);
});

test("defaultRetryDelay treats odd attempts as the first", () => {
  assert.equal(defaultRetryDelay(0), 1000);
  assert.equal(defaultRetryDelay(-3), 1000);
  assert.equal(defaultRetryDelay(Number.NaN), 1000);
});

// ---------------------------------------------------------------------------
// answerSummary

test("answer summary is the trimmed text", () => {
  assert.equal(answerSummary({ kind: "answer", text: "  Grandpa drove.  " }), "Grandpa drove.");
  assert.equal(answerSummary({ kind: "answer", text: "Grandpa\n\n drove." }), "Grandpa drove.");
});

test("answer summary keeps 80 characters and truncates longer text with an ellipsis", () => {
  const exactly80 = "a".repeat(80);
  assert.equal(answerSummary({ kind: "answer", text: exactly80 }), exactly80);

  const long = "It was the summer we drove to the lake in the blue Buick, all six of us crammed in the back.";
  const out = answerSummary({ kind: "answer", text: long });
  assert.equal(Array.from(out).length, 80);
  assert.ok(out.endsWith("…"));
  assert.ok(long.startsWith(out.slice(0, -1)));
});

test("answer summary never ends in a space before the ellipsis or splits an emoji", () => {
  const spaced = "x".repeat(78) + " yz and more";
  assert.equal(answerSummary({ kind: "answer", text: spaced }), "x".repeat(78) + "…");

  const emoji = "🙂".repeat(100);
  const out = answerSummary({ kind: "answer", text: emoji });
  assert.equal(out, "🙂".repeat(79) + "…");
});

test("answer summary of a blank answer", () => {
  assert.equal(answerSummary({ kind: "answer", text: "   " }), "No answer");
  assert.equal(answerSummary({ kind: "answer" }), "No answer");
});

test("props summary counts each action with natural plurals", () => {
  const props = [
    { id: "a", action: "keep" },
    { id: "b", action: "keep" },
    { id: "c", action: "rename", name: "Back pew" },
    { id: "d", action: "remove" },
  ];
  assert.equal(answerSummary({ kind: "props", props }), "Kept 2 props, renamed 1, removed 1");
  assert.equal(answerSummary({ kind: "props", props: [{ id: "a", action: "keep" }] }), "Kept 1 prop");
  assert.equal(
    answerSummary({
      kind: "props",
      props: [
        { id: "a", action: "rename", name: "Pew" },
        { id: "b", action: "replace_photo", photoId: "p1" },
        { id: "c", action: "replace_photo", photoId: "p2" },
      ],
    }),
    "Renamed 1 prop, replaced 2 with a photo",
  );
  assert.equal(
    answerSummary({ kind: "props", props: [{ id: "a", action: "remove" }, { id: "b", action: "remove" }] }),
    "Removed 2 props",
  );
  assert.equal(
    answerSummary({ kind: "props", props: [{ id: "a", action: "replace_photo", photoId: "p" }] }),
    "Replaced 1 prop with a photo",
  );
});

test("props summary of an empty review", () => {
  assert.equal(answerSummary({ kind: "props", props: [] }), "No changes");
  assert.equal(answerSummary({ kind: "props", props: null }), "No changes");
  assert.equal(answerSummary({ kind: "props" }), "No changes");
});

test("merge summary", () => {
  assert.equal(answerSummary({ kind: "merge", approve: true }), "Yes, the same person");
  assert.equal(answerSummary({ kind: "merge", approve: false }), "No, different people");
});

// ---------------------------------------------------------------------------
// isFinished, isFinalEvent, connectionErrorMessage

test("isFinished is true only in the terminal phases", () => {
  assert.equal(isFinished({ phase: "done" }), true);
  assert.equal(isFinished({ phase: "error" }), true);
  for (const phase of ["connecting", "running", "waiting", "scene_ready"]) {
    assert.equal(isFinished({ phase }), false, phase);
  }
});

test("isFinished follows the reducer: done, an error event, and a wake after error", () => {
  const fold = (...messages) =>
    messages.reduce((s, m) => runReducer(s, { type: "event", message: m }), initialRunState);
  assert.equal(isFinished(initialRunState), false);
  assert.equal(isFinished(fold(PLAN, START)), false);
  assert.equal(isFinished(fold(PLAN, QUESTION)), false);
  assert.equal(isFinished(fold(PLAN, DONE)), true);

  const failed = fold(PLAN, ERROR);
  assert.equal(isFinished(failed), true);
  assert.equal(failed.log.at(-1).kind, "error");

  const gaveUp = runReducer(fold(PLAN), { type: "connection_error", message: connectionErrorMessage(6) });
  assert.equal(isFinished(gaveUp), true);
});

test("isFinalEvent marks done and error only", () => {
  assert.equal(isFinalEvent("done"), true);
  assert.equal(isFinalEvent("error"), true);
  for (const e of ["plan", "question", "scene_ready", "frames", "something_new"]) {
    assert.equal(isFinalEvent(e), false, e);
  }
});

test("connectionErrorMessage is one calm, user-safe sentence", () => {
  for (const n of [0, 1, 6, 100]) {
    const m = connectionErrorMessage(n);
    assert.equal(m, "We lost touch with the studio. Try again in a moment.");
    assert.doesNotMatch(m, /error|fetch|network|http|sse|stream|\d/i);
  }
});

// ---------------------------------------------------------------------------
// nextConnectionStep and connectionMadeProgress

test("a finished or aborted connection stops", () => {
  assert.deepEqual(nextConnectionStep({ ended: "finished", failures: 3, progressed: false }), { kind: "stop" });
  assert.deepEqual(nextConnectionStep({ ended: "aborted", failures: 0, progressed: true }), { kind: "stop" });
});

test("drops back off 1s, 2s, 4s, 8s, 8s, then give up after the default 5 retries", () => {
  assert.equal(DEFAULT_MAX_RETRIES, 5);
  let failures = 0;
  const delays = [];
  for (;;) {
    const step = nextConnectionStep({ ended: "dropped", failures, progressed: false });
    if (step.kind !== "retry") {
      assert.equal(step.kind, "give_up");
      assert.equal(step.failures, 6);
      assert.equal(step.message, connectionErrorMessage(6));
      break;
    }
    assert.equal(step.attempt, step.failures);
    delays.push(step.delayMs);
    failures = step.failures;
  }
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 8000]);
});

test("a healthy connection resets the failure count", () => {
  const step = nextConnectionStep({ ended: "dropped", failures: 4, progressed: true });
  assert.deepEqual(step, { kind: "retry", attempt: 1, failures: 1, delayMs: 1000 });
});

test("maxRetries and a custom delay are honoured", () => {
  assert.equal(nextConnectionStep({ ended: "dropped", failures: 0, progressed: false }, 0).kind, "give_up");
  assert.equal(nextConnectionStep({ ended: "dropped", failures: 0, progressed: false }, -2).kind, "give_up");
  assert.equal(nextConnectionStep({ ended: "dropped", failures: 0, progressed: false }, Number.NaN).kind, "give_up");
  assert.equal(nextConnectionStep({ ended: "dropped", failures: 1, progressed: false }, 2).kind, "retry");
  assert.equal(nextConnectionStep({ ended: "dropped", failures: 2, progressed: false }, 2).kind, "give_up");
  assert.equal(nextConnectionStep({ ended: "dropped", failures: 50, progressed: false }, Infinity).kind, "retry");

  const step = nextConnectionStep({ ended: "dropped", failures: 2, progressed: false }, 5, (n) => n * 10);
  assert.deepEqual(step, { kind: "retry", attempt: 3, failures: 3, delayMs: 30 });
  assert.equal(nextConnectionStep({ ended: "dropped", failures: 0, progressed: false }, 5, () => -5).delayMs, 0);
  assert.equal(nextConnectionStep({ ended: "dropped", failures: 0, progressed: false }, 5, () => Number.NaN).delayMs, 8000);
});

test("progress means new events beyond the high-water mark, or a long-lived connection", () => {
  assert.equal(connectionMadeProgress({ highWaterBefore: 4, foldedAfter: 5, connectedMs: 10 }), true);
  assert.equal(connectionMadeProgress({ highWaterBefore: 4, foldedAfter: 4, connectedMs: 10 }), false);
  assert.equal(connectionMadeProgress({ highWaterBefore: 4, foldedAfter: 2, connectedMs: 10 }), false);
  assert.equal(
    connectionMadeProgress({ highWaterBefore: 4, foldedAfter: 4, connectedMs: HEALTHY_CONNECTION_MS }),
    true,
  );
});

// ---------------------------------------------------------------------------
// answerBody, recordAnswer, sendAnswer

test("answerBody builds each AnswerRequest shape", () => {
  assert.deepEqual(answerBody("answer", "Grandpa"), { kind: "answer", text: "Grandpa" });
  const edits = [{ id: "a", action: "keep" }];
  assert.deepEqual(answerBody("props", edits), { kind: "props", props: edits });
  assert.deepEqual(answerBody("merge", true), { kind: "merge", approve: true });
  assert.deepEqual(answerBody("merge", false), { kind: "merge", approve: false });
});

test("recordAnswer notes where the answer was given", () => {
  const book = { folded: 3, highWater: 3, answers: [] };
  assert.deepEqual(recordAnswer(book, "answer", "Grandpa"), { type: "answered", kind: "answer", summary: "Grandpa" });
  assert.deepEqual(book.answers, [{ after: 3, kind: "answer", summary: "Grandpa" }]);
  // A replay reset the count while the post was in flight: keep the send position.
  book.folded = 0;
  recordAnswer(book, "merge", "Yes, the same person", 5);
  assert.equal(book.answers[1].after, 5);
});

test("sendAnswer posts, then dispatches answered with the summary", async () => {
  const posted = [];
  const actions = [];
  const book = { folded: 2, highWater: 2, answers: [] };
  const sent = await sendAnswer({
    answer: async (body) => {
      posted.push(body);
      assert.equal(actions.length, 0, "nothing dispatched before the post resolves");
    },
    body: answerBody("props", [{ id: "a", action: "keep" }, { id: "b", action: "remove" }]),
    pendingKind: "props",
    currentBook: () => book,
    dispatch: (a) => actions.push(a),
  });
  assert.equal(sent, true);
  assert.deepEqual(posted, [{ kind: "props", props: [{ id: "a", action: "keep" }, { id: "b", action: "remove" }] }]);
  assert.deepEqual(actions, [{ type: "answered", kind: "props", summary: "Kept 1 prop, removed 1" }]);
  assert.equal(book.answers.length, 1);
});

test("sendAnswer rethrows a failed post and dispatches nothing", async () => {
  const actions = [];
  const book = newRunBook();
  await assert.rejects(
    sendAnswer({
      answer: async () => {
        throw new Error("That didn't reach the studio.");
      },
      body: answerBody("answer", "Grandpa"),
      pendingKind: "answer",
      currentBook: () => book,
      dispatch: (a) => actions.push(a),
    }),
    { message: "That didn't reach the studio." },
  );
  assert.deepEqual(actions, []);
  assert.deepEqual(book.answers, []);
});

test("sendAnswer ignores a call the run is not waiting on", async () => {
  for (const pendingKind of [null, "props", "merge"]) {
    let posts = 0;
    const actions = [];
    const sent = await sendAnswer({
      answer: async () => {
        posts += 1;
      },
      body: answerBody("answer", "Grandpa"),
      pendingKind,
      currentBook: () => newRunBook(),
      dispatch: (a) => actions.push(a),
    });
    assert.equal(sent, false);
    assert.equal(posts, 0);
    assert.deepEqual(actions, []);
  }
});

/** An error shaped like the API client's ApiError (status + message). */
function httpError(status, message) {
  const e = new Error(message);
  e.status = status;
  return e;
}

test("sendAnswer: a 409 means already answered: it resolves and closes the pause quietly", async () => {
  const raw = "run x is running, not waiting for an answer";
  let state = [PLAN, QUESTION].reduce((s, m) => runReducer(s, { type: "event", message: m }), initialRunState);
  assert.equal(state.phase, "waiting");
  const actions = [];
  const book = { folded: 2, highWater: 2, answers: [] };
  const sent = await sendAnswer({
    answer: async () => {
      throw { status: 409, message: raw };
    },
    body: answerBody("answer", "Grandpa"),
    pendingKind: "answer",
    currentBook: () => book,
    dispatch: (a) => {
      actions.push(a);
      state = runReducer(state, a);
    },
  });
  assert.equal(sent, true);
  assert.deepEqual(actions, [{ type: "answered", kind: "answer", summary: null }]);
  assert.equal(state.pending, null);
  assert.equal(state.phase, "running");
  assert.doesNotMatch(JSON.stringify(state.log), /not waiting|running, not/);
  assert.doesNotMatch(JSON.stringify(actions), /not waiting/);
  // Recorded, so a replay closes the pause again at the same point.
  assert.deepEqual(book.answers, [{ after: 2, kind: "answer", summary: null }]);

  // An ApiError-like instance works the same way.
  const again = await sendAnswer({
    answer: async () => {
      throw httpError(409, raw);
    },
    body: answerBody("props", []),
    pendingKind: "props",
    currentBook: () => null,
    dispatch: () => assert.fail("nothing to dispatch for a swapped-out run"),
  });
  assert.equal(again, true);
});

test("sendAnswer: other failures still reject with their message", async () => {
  for (const status of [503, 500, 0, 422, undefined]) {
    const actions = [];
    const book = newRunBook();
    await assert.rejects(
      sendAnswer({
        answer: async () => {
          throw httpError(status, "The studio is busy. Try again.");
        },
        body: answerBody("merge", true),
        pendingKind: "merge",
        currentBook: () => book,
        dispatch: (a) => actions.push(a),
      }),
      { message: "The studio is busy. Try again." },
    );
    assert.deepEqual(actions, [], String(status));
    assert.deepEqual(book.answers, []);
  }
});

test("isNotWaiting duck-types status 409 only", () => {
  assert.equal(isNotWaiting({ status: 409 }), true);
  assert.equal(isNotWaiting(httpError(409, "x")), true);
  for (const e of [null, undefined, "409", 409, { status: "409" }, { status: 503 }, new Error("409")]) {
    assert.equal(isNotWaiting(e), false, String(e));
  }
});

test("replay after a 409 closes the replayed pause again without a second line", async () => {
  const book = newRunBook();
  const { stream } = fakeStream([
    [PLAN, QUESTION, "throw"],
    [PLAN, QUESTION, DIRECTOR, DONE],
  ]);
  let state = initialRunState;
  const dispatch = (a) => {
    state = runReducer(state, a);
    if (a.type === "event" && a.message.event === "question" && book.answers.length === 0) {
      dispatch(recordAnswer(book, "answer", null));
    }
  };
  await followRun({
    stream,
    book,
    dispatch,
    signal: new AbortController().signal,
    replayOnReconnect: true,
    maxRetries: 5,
    retryDelayMs: defaultRetryDelay,
    sleep: async () => {},
  });
  assert.equal(state.phase, "done");
  assert.deepEqual(
    state.log.filter((e) => e.kind === "answered").map((e) => e.summary),
    [null],
  );
});

test("replay re-applies an answer recorded after the run moved on: one line, the family's words", async () => {
  // The POST resolved only after the next sub-agent had started, so the
  // answer is recorded past the event that closed the pause.
  const book = newRunBook();
  const { stream } = fakeStream([
    [PLAN, QUESTION, DIRECTOR, "throw"],
    [PLAN, QUESTION, DIRECTOR, DONE],
  ]);
  let state = initialRunState;
  const dispatch = (a) => {
    state = runReducer(state, a);
    if (a.type === "event" && a.message.event === "subagent_start" && book.answers.length === 0) {
      dispatch(recordAnswer(book, "answer", "Grandpa drove", 2));
    }
  };
  await followRun({
    stream,
    book,
    dispatch,
    signal: new AbortController().signal,
    replayOnReconnect: true,
    maxRetries: 5,
    retryDelayMs: defaultRetryDelay,
    sleep: async () => {},
  });
  assert.equal(state.phase, "done");
  assert.deepEqual(
    state.log.filter((e) => e.kind === "answered").map((e) => e.summary),
    ["Grandpa drove"],
  );
});

test("sendAnswer does not dispatch for a run that was swapped out meanwhile", async () => {
  let current = newRunBook();
  const actions = [];
  const sent = await sendAnswer({
    answer: async () => {
      current = null; // unmounted or a new source while the post was in flight
    },
    body: answerBody("merge", true),
    pendingKind: "merge",
    currentBook: () => current,
    dispatch: (a) => actions.push(a),
  });
  assert.equal(sent, true);
  assert.deepEqual(actions, []);
});

// ---------------------------------------------------------------------------
// followRun: the reconnect loop, driven with fake streams and no timers

/**
 * A fake RunSource stream. Each connection plays the next script: a list of
 * messages, then "throw", "end" or "hang" (stays open until aborted).
 */
function fakeStream(scripts) {
  const calls = [];
  function stream(signal) {
    const script = scripts[Math.min(calls.length, scripts.length - 1)];
    const call = { signal, yielded: 0, closed: false };
    calls.push(call);
    return (async function* () {
      try {
        for (const item of script) {
          if (signal.aborted) return;
          if (item === "throw") throw new TypeError("network error");
          if (item === "end") return;
          if (item === "hang") {
            await new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true }));
            return;
          }
          await Promise.resolve();
          call.yielded += 1;
          yield item;
        }
      } finally {
        call.closed = true;
      }
    })();
  }
  return { stream, calls };
}

/** Runs followRun with a recording dispatch and sleep; returns what happened. */
async function follow(scripts, opts = {}) {
  const { stream, calls } = fakeStream(scripts);
  const actions = [];
  const sleeps = [];
  const controller = opts.controller ?? new AbortController();
  const book = opts.book ?? newRunBook();
  let state = initialRunState;
  await followRun({
    stream,
    book,
    dispatch: (a) => {
      actions.push(a);
      state = runReducer(state, a);
      opts.onDispatch?.(a, { book, controller, state });
    },
    signal: controller.signal,
    replayOnReconnect: opts.replay ?? true,
    maxRetries: opts.maxRetries ?? 5,
    retryDelayMs: opts.retryDelayMs ?? defaultRetryDelay,
    sleep: async (ms, signal) => {
      sleeps.push(ms);
      await opts.onSleep?.(ms, { controller, signal });
    },
    now: opts.now ?? (() => 0),
  });
  return { actions, sleeps, calls, state, book };
}

const types = (actions) => actions.map((a) => (a.type === "event" ? a.message.event : a.type));

test("a run that streams to done connects once and stops", async () => {
  const r = await follow([[PLAN, START, END, DONE, "hang"]]);
  assert.equal(r.calls.length, 1);
  assert.deepEqual(types(r.actions), ["plan", "subagent_start", "subagent_end", "done"]);
  assert.deepEqual(r.sleeps, []);
  assert.equal(r.state.phase, "done");
  assert.equal(r.calls[0].signal.aborted, true, "the finished stream's connection is closed");
  assert.equal(r.calls[0].closed, true);
});

test("an error event ends the run and nothing after it is read", async () => {
  const r = await follow([[PLAN, ERROR, START, "hang"]]);
  assert.equal(r.calls.length, 1);
  assert.deepEqual(types(r.actions), ["plan", "error"]);
  assert.equal(r.state.phase, "error");
  assert.equal(r.state.error, "The studio stopped.");
});

test("a drop reconnects, resets once, and refolds the replayed log without duplicates", async () => {
  const r = await follow([
    [PLAN, START, "throw"],
    [PLAN, START, END, DONE],
  ]);
  assert.equal(r.calls.length, 2);
  assert.deepEqual(r.sleeps, [1000]);
  assert.deepEqual(types(r.actions), [
    "plan", "subagent_start",
    "reset", "plan", "subagent_start", "subagent_end", "done",
  ]);
  const once = [PLAN, START, END, DONE].reduce(
    (s, m) => runReducer(s, { type: "event", message: m }),
    initialRunState,
  );
  assert.deepEqual(r.state, once);
  assert.equal(r.state.steps.length, 1);
});

test("a stream that closes before done counts as a drop", async () => {
  const r = await follow([[PLAN, "end"], [PLAN, DONE]]);
  assert.equal(r.calls.length, 2);
  assert.deepEqual(r.sleeps, [1000]);
  assert.equal(r.state.phase, "done");
});

test("a reconnect that delivers nothing does not reset the feed", async () => {
  const r = await follow([[PLAN, START, "throw"], ["throw"], [PLAN, START, DONE]]);
  assert.deepEqual(types(r.actions), [
    "plan", "subagent_start",
    "reset", "plan", "subagent_start", "done",
  ]);
  // The first drop follows progress (attempt 1); the empty reconnect is the second failure in a row.
  assert.deepEqual(r.sleeps, [1000, 2000]);
});

test("without replay a reconnect resumes: no reset, new events append", async () => {
  const r = await follow([[PLAN, START, "throw"], [END, DONE]], { replay: false });
  assert.deepEqual(types(r.actions), ["plan", "subagent_start", "subagent_end", "done"]);
  assert.equal(r.state.steps[0].status, "done");
});

test("after the last retry it dispatches one connection_error and stops", async () => {
  const r = await follow([["throw"]], { maxRetries: 3 });
  assert.equal(r.calls.length, 4, "the first try plus three retries");
  assert.deepEqual(r.sleeps, [1000, 2000, 4000]);
  assert.deepEqual(r.actions, [{ type: "connection_error", message: connectionErrorMessage(4) }]);
  assert.equal(r.state.phase, "error");
  assert.equal(r.state.error, "We lost touch with the studio. Try again in a moment.");
});

test("the default policy gives up after five retries with the default delays", async () => {
  const r = await follow([["throw"]]);
  assert.equal(r.calls.length, 6);
  assert.deepEqual(r.sleeps, [1000, 2000, 4000, 8000, 8000]);
  assert.deepEqual(types(r.actions), ["connection_error"]);
});

test("a custom retry delay is used", async () => {
  const r = await follow([["throw"]], { maxRetries: 2, retryDelayMs: (n) => n * 7 });
  assert.deepEqual(r.sleeps, [7, 14]);
});

test("a run that keeps making progress keeps reconnecting with the short delay", async () => {
  // Each connection replays what came before and adds one new event, then drops.
  const log = [PLAN, START, END, msg("subagent_start", { name: "storyboarder" }), msg("subagent_end", { name: "storyboarder" }), DIRECTOR, msg("subagent_end", { name: "director" })];
  const scripts = log.map((_, i) => [...log.slice(0, i + 1), "throw"]);
  scripts.push([...log, DONE]);
  const r = await follow(scripts, { maxRetries: 2 });
  assert.equal(r.calls.length, log.length + 1);
  assert.ok(r.sleeps.every((ms) => ms === 1000));
  assert.equal(r.state.phase, "done");
  assert.equal(r.state.steps.length, 3);
});

/**
 * Connection 3 replays the paused run (nothing new) and drops; with
 * `quietMs` it was open that long first, as when the family takes a while.
 */
async function quietRun(quietMs) {
  let clock = 0;
  let connection = 0;
  const { stream } = fakeStream([
    [PLAN, QUESTION, "throw"],
    ["throw"],
    [PLAN, QUESTION, "throw"],
    ["throw"],
    [PLAN, QUESTION, DONE],
  ]);
  const actions = [];
  const sleeps = [];
  await followRun({
    stream: (signal) => {
      connection += 1;
      if (connection === 3) clock += quietMs;
      return stream(signal);
    },
    book: newRunBook(),
    dispatch: (a) => actions.push(a),
    signal: new AbortController().signal,
    replayOnReconnect: true,
    maxRetries: 2,
    retryDelayMs: defaultRetryDelay,
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    now: () => clock,
  });
  return { connections: connection, sleeps, last: types(actions).at(-1) };
}

test("a long-lived quiet connection resets the retry budget", async () => {
  const quick = await quietRun(0);
  assert.equal(quick.connections, 3, "a quick, empty replay is the third drop in a row: out of retries");
  assert.deepEqual(quick.sleeps, [1000, 2000]);
  assert.equal(quick.last, "connection_error");

  const patient = await quietRun(HEALTHY_CONNECTION_MS + 10_000);
  assert.equal(patient.connections, 5);
  assert.deepEqual(patient.sleeps, [1000, 2000, 1000, 2000]);
  assert.equal(patient.last, "done");
});

test("replay re-applies a recorded answer exactly once, after the question", async () => {
  const book = newRunBook();
  const { stream, calls } = fakeStream([
    [PLAN, QUESTION, "throw"],
    [PLAN, QUESTION, DIRECTOR, DONE],
  ]);
  const actions = [];
  let state = initialRunState;
  const dispatch = (a) => {
    actions.push(a);
    state = runReducer(state, a);
    // The family answers the question the first time it shows.
    if (a.type === "event" && a.message.event === "question" && book.answers.length === 0) {
      dispatch(recordAnswer(book, "answer", "Grandpa drove"));
    }
  };
  await followRun({
    stream,
    book,
    dispatch,
    signal: new AbortController().signal,
    replayOnReconnect: true,
    maxRetries: 5,
    retryDelayMs: defaultRetryDelay,
    sleep: async () => {},
  });
  assert.equal(calls.length, 2);
  assert.deepEqual(types(actions), [
    "plan", "question", "answered",
    "reset", "plan", "question", "answered", "subagent_start", "done",
  ]);
  assert.equal(state.phase, "done");
  assert.equal(state.pending, null);
  const answered = state.log.filter((e) => e.kind === "answered");
  assert.deepEqual(answered.map((e) => e.summary), ["Grandpa drove"]);
  const kinds = state.log.map((e) => e.kind);
  assert.ok(kinds.indexOf("asked") < kinds.indexOf("answered"));
  assert.ok(kinds.indexOf("answered") < kinds.indexOf("step", kinds.indexOf("answered")));
});

test("aborting mid-stream stops at once: nothing dispatched afterwards, no error", async () => {
  const controller = new AbortController();
  const r = await follow([[PLAN, START, END, DONE]], {
    controller,
    onDispatch: (a) => {
      if (a.type === "event" && a.message.event === "plan") controller.abort();
    },
  });
  assert.deepEqual(types(r.actions), ["plan"]);
  assert.equal(r.calls.length, 1);
  assert.equal(r.calls[0].signal.aborted, true, "the abort reaches the stream");
  assert.deepEqual(r.sleeps, []);
});

test("aborting a hanging stream ends the loop quietly", async () => {
  const controller = new AbortController();
  const pending = follow([[PLAN, "hang"]], { controller });
  await new Promise((resolve) => setTimeout(resolve, 5));
  controller.abort();
  const r = await pending;
  assert.deepEqual(types(r.actions), ["plan"]);
  assert.equal(r.calls.length, 1);
  assert.deepEqual(r.sleeps, []);
});

test("aborting during the backoff wait never reconnects or reports an error", async () => {
  const controller = new AbortController();
  const r = await follow([["throw"]], {
    controller,
    onSleep: () => controller.abort(),
  });
  assert.equal(r.calls.length, 1);
  assert.deepEqual(r.sleeps, [1000]);
  assert.deepEqual(r.actions, []);
});

test("an already-aborted signal never connects", async () => {
  const controller = new AbortController();
  controller.abort();
  const r = await follow([[PLAN, DONE]], { controller });
  assert.equal(r.calls.length, 0);
  assert.deepEqual(r.actions, []);
});

test("unknown events are folded harmlessly and counted for replay", async () => {
  const NEW = msg("sound_check", { ok: true });
  const r = await follow([[PLAN, NEW, "throw"], [PLAN, NEW, START, DONE]]);
  assert.deepEqual(r.sleeps, [1000]);
  assert.equal(r.book.highWater, 4);
  assert.equal(r.state.phase, "done");
});

// ---------------------------------------------------------------------------
// Saved answers: a reload in the same tab does not bring an answered card back

test("serializeAnswers and parseAnswers round-trip the records", () => {
  const answers = [
    { after: 3, kind: "answer", summary: "Grandpa drove" },
    { after: 7, kind: "props", summary: null },
    { after: 9, kind: "merge", summary: "Yes, the same person" },
  ];
  assert.deepEqual(parseAnswers(serializeAnswers(answers)), answers);
  assert.equal(answersStorageKey("fam/run1"), "stitch:run-answers:fam/run1");
  assert.notEqual(answersStorageKey("fam/run1"), answersStorageKey("fam/run2"));
});

test("parseAnswers keeps only well-formed records and survives junk", () => {
  for (const raw of [null, undefined, "", "not json", "{}", "42", '"x"', "null"]) {
    assert.deepEqual(parseAnswers(raw), [], String(raw));
  }
  const raw = JSON.stringify([
    { after: 2, kind: "answer", summary: "ok" },
    { after: 0, kind: "answer", summary: "zero" },
    { after: 1.5, kind: "answer", summary: "half" },
    { after: "3", kind: "answer", summary: "string" },
    { after: 4, kind: "shout", summary: "kind" },
    { after: 5, kind: "props", summary: 12 },
    null,
    "x",
    { after: 6, kind: "merge", summary: null, extra: "<b>" },
  ]);
  assert.deepEqual(parseAnswers(raw), [
    { after: 2, kind: "answer", summary: "ok" },
    { after: 6, kind: "merge", summary: null },
  ]);
  // Long summaries are cut to the usual length; long lists keep the latest.
  const [long] = parseAnswers(JSON.stringify([{ after: 1, kind: "answer", summary: "a".repeat(500) }]));
  assert.ok(long.summary.length <= 80);
  const many = Array.from({ length: MAX_SAVED_ANSWERS + 10 }, (_, i) => ({ after: i + 1, kind: "answer", summary: null }));
  const kept = parseAnswers(JSON.stringify(many));
  assert.equal(kept.length, MAX_SAVED_ANSWERS);
  assert.equal(kept.at(-1).after, MAX_SAVED_ANSWERS + 10);
});

test("newRunBook starts from saved answers without sharing them", () => {
  const saved = [{ after: 3, kind: "answer", summary: "Grandpa" }];
  const book = newRunBook(saved);
  assert.deepEqual(book, { folded: 0, highWater: 0, answers: saved });
  book.answers[0].after = 99;
  assert.equal(saved[0].after, 3);
  assert.deepEqual(newRunBook(), { folded: 0, highWater: 0, answers: [] });
});

test("sendAnswer hands the book to onRecorded once the answer is recorded", async () => {
  const book = { folded: 2, highWater: 2, answers: [] };
  const saved = [];
  await sendAnswer({
    answer: async () => {},
    body: answerBody("answer", "Grandpa"),
    pendingKind: "answer",
    currentBook: () => book,
    dispatch: () => {},
    onRecorded: (b) => saved.push(serializeAnswers(b.answers)),
  });
  // A 409 is recorded (and saved) too; any other failure saves nothing.
  await sendAnswer({
    answer: async () => {
      throw httpError(409, "run x is running, not waiting for an answer");
    },
    body: answerBody("merge", true),
    pendingKind: "merge",
    currentBook: () => book,
    dispatch: () => {},
    onRecorded: (b) => saved.push(serializeAnswers(b.answers)),
  });
  await assert.rejects(
    sendAnswer({
      answer: async () => {
        throw httpError(503, "busy");
      },
      body: answerBody("merge", true),
      pendingKind: "merge",
      currentBook: () => book,
      dispatch: () => {},
      onRecorded: () => assert.fail("a failed answer is not saved"),
    }),
  );
  assert.equal(saved.length, 2);
  assert.deepEqual(parseAnswers(saved[1]), [
    { after: 2, kind: "answer", summary: "Grandpa" },
    { after: 2, kind: "merge", summary: null },
  ]);
  // Nothing is saved for a run that was swapped out meanwhile.
  await sendAnswer({
    answer: async () => {},
    body: answerBody("answer", "x"),
    pendingKind: "answer",
    currentBook: () => null,
    dispatch: () => {},
    onRecorded: () => assert.fail("no book, nothing to save"),
  });
});

/**
 * One page load following `script` (which must end in "hang"): stops once
 * every message has been folded. `onDispatch` sees each action and state.
 */
async function pageLoad(script, book, onDispatch) {
  const messages = script.filter((m) => typeof m !== "string").length;
  return follow([script], {
    book,
    onDispatch: (a, ctx) => {
      onDispatch?.(a, ctx);
      if (a.type === "event" && ctx.book.folded === messages) ctx.controller.abort();
    },
  });
}

test("a reload right after answering replays the question but shows no card: the saved answer closes it", async () => {
  const storage = new Map();
  const key = answersStorageKey("fam/run1");
  // Before the reload: the run pauses on the question and the family answers.
  const first = newRunBook();
  let state = initialRunState;
  await pageLoad([PLAN, START, QUESTION, "hang"], first, (a, ctx) => {
    state = ctx.state;
  });
  assert.equal(state.phase, "waiting");
  await sendAnswer({
    answer: async () => {},
    body: answerBody("answer", "Grandpa drove"),
    pendingKind: "answer",
    currentBook: () => first,
    dispatch: () => {},
    onRecorded: (b) => storage.set(key, serializeAnswers(b.answers)),
  });

  // The reload: a fresh book from storage, and the server replays the run,
  // which has not yet moved past the paused tool call.
  const r = await pageLoad([PLAN, START, QUESTION, "hang"], newRunBook(parseAnswers(storage.get(key))));
  assert.equal(r.state.phase, "running");
  assert.equal(r.state.pending, null);
  const answered = r.state.log.filter((e) => e.kind === "answered");
  assert.deepEqual(answered.map((e) => [e.answer, e.summary]), [["answer", "Grandpa drove"]]);
  // Closed in the same breath as the question arrives: no state in between
  // where the card could flash.
  const i = r.actions.findIndex((a) => a.type === "event" && a.message.event === "question");
  assert.deepEqual(r.actions[i + 1], { type: "answered", kind: "answer", summary: "Grandpa drove" });

  // Later, when the run's next step also closes the pause, still one line.
  const later = await pageLoad(
    [PLAN, START, QUESTION, PLAN, END, DIRECTOR, "hang"],
    newRunBook(parseAnswers(storage.get(key))),
  );
  assert.equal(later.state.pending, null);
  assert.deepEqual(
    later.state.log.filter((e) => e.kind === "answered").map((e) => e.summary),
    ["Grandpa drove"],
  );
});

test("saved answers for one run never close another run's pause", async () => {
  const storage = new Map([[answersStorageKey("fam/run1"), serializeAnswers([{ after: 3, kind: "answer", summary: "x" }])]]);
  const r = await pageLoad(
    [PLAN, START, QUESTION, "hang"],
    newRunBook(parseAnswers(storage.get(answersStorageKey("fam/run2")) ?? null)),
  );
  assert.equal(r.state.phase, "waiting");
  assert.equal(r.state.pending?.kind, "answer");
});
