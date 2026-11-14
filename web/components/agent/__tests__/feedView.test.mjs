// Run with: cd web && node --test "components/agent/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in feedView.ts, runState.ts and
// runConnection.ts. States are built with the real reducer, and answers with
// the real answerSummary, so the wording is checked end to end.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  answeredLine,
  compileNote,
  errorRecovery,
  finishedHeading,
  finishedNote,
  openLabel,
  readyLine,
  workingHeading,
  hiddenEntryCount,
  pauseLine,
  phaseLine,
  RECONNECTING_LINE,
  startOverNote,
  stepDetail,
  toggleLabel,
  visibleEntries,
} from "../feedView.ts";
import { answerSummary } from "../runConnection.ts";
import { initialRunState, runReducer } from "../runState.ts";

const ev = (event, data) => ({ type: "event", message: { event, data } });
const answered = (body) => ({ type: "answered", kind: body.kind, summary: answerSummary(body) });
const run = (...actions) => actions.reduce(runReducer, initialRunState);

const plan = (at) => ev("plan", {
  todos: ["Listen", "Props", "Stage"].map((text, i) => ({
    text,
    status: i < at ? "completed" : i === at ? "in_progress" : "pending",
  })),
});
const CHIPS = [
  { id: "p1", name: "hymn book", size: "hand" },
  { id: "p2", name: "station wagon", size: "vehicle" },
];
const COMPILE = {
  round: 2,
  errors: [
    { code: "not_holding", message: 'shot s2, t=4.5: mom cannot hand_to "hymn_books"; they are not holding it' },
    { code: "unknown_mark", message: "shot s3: unknown mark porch" },
  ],
};

/**
 * The plain text of the log as the family reads it: one string per line,
 * built from the same helpers AgentFeed renders with.
 */
function logText(state, showAll) {
  const steps = new Map(state.steps.map((s) => [s.key, s]));
  return visibleEntries(state, showAll).map((e) => {
    switch (e.kind) {
      case "step": {
        const step = steps.get(e.stepKey);
        return [step.name, stepDetail(state, step)].filter(Boolean).join(": ");
      }
      case "answered": {
        const { prompt, text } = answeredLine(e);
        return [prompt, text].filter(Boolean).join(" ");
      }
      case "asked":
        return `The studio asked: ${pauseLine(e)}`;
      case "props":
      case "merge":
        return pauseLine(e);
      case "compile_error":
        return compileNote(e.event);
      case "frames":
        return `[frames] ${e.event.critique ?? ""}`;
      case "scene_ready":
        return "The scene is stitched together.";
      case "error":
        return e.message;
      default:
        return "";
    }
  });
}

// ---------------------------------------------------------------------------
// Words

test("compileNote is one friendly line with no error text or codes", () => {
  const note = compileNote(COMPILE);
  assert.equal(note, "The director is tidying up a few things (round 2).");
  for (const err of COMPILE.errors) {
    assert.ok(!note.includes(err.code));
    assert.ok(!note.includes(err.message));
  }
  assert.doesNotMatch(note, /hand_to|hymn|shot|not_holding|unknown|t=/);
  assert.equal(compileNote({ round: null }), "The director is tidying up a few things.");
  assert.equal(compileNote({}), "The director is tidying up a few things.");
});

test("compile errors render as the one note in the log", () => {
  const s = run(ev("compile_error", COMPILE));
  const text = logText(s, true).join("\n");
  assert.equal(text, "The director is tidying up a few things (round 2).");
});

test("phaseLine names each kind of pause differently", () => {
  const answer = phaseLine(run(ev("question", { text: "Who?" })));
  const props = phaseLine(run(ev("props_found", { props: CHIPS })));
  const merge = phaseLine(run(ev("merge", { text: "Same?", personId: "p", candidateName: "Joe" })));
  assert.equal(answer, "The studio has a question for you.");
  assert.equal(new Set([answer, props, merge]).size, 3);
  assert.match(props, /things in your story/);
  assert.match(merge, /check someone/);
});

test("phaseLine: running, none when done, never the question line once moved on", () => {
  assert.equal(phaseLine(run(plan(0))), "The studio is working on your moment.");
  assert.equal(phaseLine(run(ev("done", { memoryId: "m" }))), null);
  assert.equal(phaseLine(initialRunState), "Opening the studio…");
  const reloaded = run(
    plan(0),
    ev("subagent_start", { name: "interviewer" }),
    ev("subagent_end", { name: "interviewer" }),
    ev("question", { text: "Who drove?" }),
    plan(1),
    ev("subagent_start", { name: "prop_master" }),
  );
  assert.notEqual(phaseLine(reloaded), "The studio has a question for you.");
});

test("finishedHeading names what was added", () => {
  assert.equal(finishedHeading("memory"), "Your memory is stitched in");
  assert.equal(finishedHeading(), "Your memory is stitched in");
  assert.match(finishedHeading("telling"), /telling/);
  assert.notEqual(finishedHeading("telling"), finishedHeading("memory"));
});

test("finishedNote is one sentence", () => {
  assert.equal(finishedNote({ memoryId: "m" }), "Your page is ready, and it’s in everyone’s books.");
  assert.match(finishedNote({ memoryId: null }), /Thank you/);
  assert.match(finishedNote({ memoryId: "m" }, "telling"), /your own words/);
  assert.match(finishedNote({ memoryId: null }, "telling"), /Thank you/);
});

test("workingHeading and openLabel follow what the run adds", () => {
  assert.equal(workingHeading(), "Making your page");
  assert.equal(workingHeading("memory"), "Making your page");
  assert.equal(workingHeading("telling"), "Adding your version");
  assert.equal(openLabel(), "Open your page");
  assert.equal(openLabel("telling"), "Open the moment");
  assert.equal(readyLine(), "Your page is ready");
  assert.equal(readyLine("telling"), "Your version is on the page");
});

test("answeredLine phrases each kind", () => {
  const q = run(ev("question", { text: "Who drove?" }), answered({ kind: "answer", text: "Grandpa" }));
  assert.deepEqual(answeredLine(q.log.at(-1)), { prompt: "Who drove?", text: "You answered: “Grandpa”" });

  const p = run(
    ev("props_found", { props: CHIPS }),
    answered({ kind: "props", props: [{ id: "p1", action: "keep" }, { id: "p2", action: "keep" }, { id: "p3", action: "rename", name: "Pew" }] }),
  );
  assert.deepEqual(answeredLine(p.log.at(-1)), { prompt: null, text: "You looked over the props: kept 2, renamed 1" });

  const one = run(ev("props_found", { props: CHIPS }), answered({ kind: "props", props: [{ id: "p1", action: "replace_photo", photoId: "x" }] }));
  assert.equal(answeredLine(one.log.at(-1)).text, "You looked over the props: replaced 1 with a photo");

  const none = run(ev("props_found", { props: CHIPS }), answered({ kind: "props", props: [] }));
  assert.equal(answeredLine(none.log.at(-1)).text, "You looked over the props and kept them as they were.");

  const MERGE = { text: "Is Uncle Joe the Joseph in the tree?", personId: "p", candidateName: "Uncle Joe" };
  const yes = run(ev("merge", MERGE), answered({ kind: "merge", approve: true }));
  assert.deepEqual(answeredLine(yes.log.at(-1)), { prompt: MERGE.text, text: "You said they’re the same person." });
  const no = run(ev("merge", MERGE), answered({ kind: "merge", approve: false }));
  assert.equal(answeredLine(no.log.at(-1)).text, "You said they’re different people.");
});

test("answeredLine: a pause answered elsewhere reads as answered earlier", () => {
  const q = run(ev("question", { text: "Who drove?" }), ev("subagent_start", { name: "x" }));
  const entry = q.log.find((e) => e.kind === "answered");
  assert.deepEqual(answeredLine(entry), { prompt: "Who drove?", text: "Answered earlier." });
  const p = run(ev("props_found", { props: CHIPS }), ev("subagent_start", { name: "x" }));
  assert.equal(answeredLine(p.log.find((e) => e.kind === "answered")).text, "You looked over the props earlier.");
});

// ---------------------------------------------------------------------------
// Which lines show

test("with a plan, step rows fold behind Show all steps; the rest stays", () => {
  const s = run(
    plan(0),
    ev("subagent_start", { name: "interviewer", task: "Read it" }),
    ev("subagent_end", { name: "interviewer", summary: "Heard it" }),
    ev("compile_error", COMPILE),
    ev("frames", { frames: [{ t: 1, mediaId: "m1" }], critique: "Dad is hidden" }),
    ev("frames", { frames: [{ t: 2, mediaId: "m2" }] }),
    ev("subagent_start", { name: "director" }),
  );
  const shown = visibleEntries(s, false).map((e) => e.kind);
  assert.deepEqual(shown, ["compile_error", "frames"]);
  assert.equal(visibleEntries(s, false)[1].event.critique, "Dad is hidden");
  assert.equal(hiddenEntryCount(s), 3, "two steps and the plain frames");
  assert.deepEqual(
    visibleEntries(s, true).map((e) => e.kind),
    ["step", "compile_error", "frames", "frames", "step"],
  );
  assert.equal(toggleLabel(s, false, 3), "Show all steps (3)");
  assert.equal(toggleLabel(s, true, 3), "Hide the steps");
});

test("without a plan, step rows show as before (older finished ones fold)", () => {
  const steps = [];
  for (const name of ["a", "b", "c", "d", "e", "f"]) {
    steps.push(ev("subagent_start", { name }), ev("subagent_end", { name }));
  }
  const s = run(...steps);
  assert.equal(visibleEntries(s, false).length, 4);
  assert.equal(hiddenEntryCount(s), 2);
  assert.equal(visibleEntries(s, true).length, 6);
  assert.equal(toggleLabel(s, false, 2), "Show all steps (2 earlier)");
});

test("the open pause's own line hides while its card is up", () => {
  for (const pause of [
    ev("question", { text: "Who drove?" }),
    ev("props_found", { props: CHIPS }),
    ev("merge", { text: "Same?", personId: "p", candidateName: "Joe" }),
  ]) {
    const s = run(plan(0), ev("subagent_start", { name: "x" }), pause);
    assert.equal(s.phase, "waiting");
    assert.deepEqual(visibleEntries(s, true).map((e) => e.kind), ["step"], pause.message.event);
    assert.equal(hiddenEntryCount(s), 1);
  }
});

test("a pause shows when its card cannot (the connection dropped mid-pause)", () => {
  const s = run(ev("question", { text: "Who drove?" }), { type: "connection_error", message: "Lost." });
  const kinds = visibleEntries(s, false).map((e) => e.kind);
  assert.deepEqual(kinds, ["asked"], "the error line is in the error box instead");
});

test("once answered, one line for the exchange", () => {
  const s = run(ev("question", { text: "Who drove?" }), answered({ kind: "answer", text: "Grandpa" }));
  assert.deepEqual(logText(s, true), ["Who drove? You answered: “Grandpa”"]);
  const moved = run(ev("question", { text: "Who drove?" }), ev("subagent_start", { name: "storyboarder" }));
  assert.deepEqual(logText(moved, true), ["Who drove? Answered earlier.", "storyboarder"]);
});

test("done: no scene_ready line, everything folded behind Show all steps", () => {
  const s = run(
    plan(0),
    ev("subagent_start", { name: "checker" }),
    ev("subagent_end", { name: "checker", summary: "Looks right" }),
    ev("frames", { frames: [{ t: 1, mediaId: "m1" }], critique: "Good" }),
    ev("scene_ready", { memoryId: "m1" }),
    ev("subagent_start", { name: "linker" }),
    ev("subagent_end", { name: "linker" }),
    ev("done", { memoryId: "m1" }),
  );
  assert.deepEqual(visibleEntries(s, false), []);
  assert.ok(hiddenEntryCount(s) > 0);
  assert.ok(!visibleEntries(s, true).some((e) => e.kind === "scene_ready"));
  assert.ok(!logText(s, true).some((t) => /stitched together/.test(t)));
  assert.equal(toggleLabel(s, false, hiddenEntryCount(s)), "Show all steps");
  // While the scene is ready but the run goes on, the panel says so instead.
  const ready = run(ev("scene_ready", { memoryId: "m1" }));
  assert.deepEqual(visibleEntries(ready, true), []);
});

test("the latest error shows in the error box, not twice", () => {
  const s = run(ev("error", { message: "The director gave up" }));
  assert.deepEqual(visibleEntries(s, true), []);
});

// The mock's props step: prop_master starts, pauses on the props, the family
// keeps two, and the step ends with "Kept 2 props.".
function propsRun(answer) {
  return run(
    plan(1),
    ev("subagent_start", { name: "prop_master", task: "Find the things in the story" }),
    ev("props_found", { props: CHIPS }),
    ...(answer ? [answered({ kind: "props", props: [{ id: "p1", action: "keep" }, { id: "p2", action: "keep" }] })] : []),
    ev("subagent_end", { name: "prop_master", summary: "Kept 2 props." }),
    plan(2),
    ev("subagent_start", { name: "director", task: "Block the scene" }),
  );
}

test("props fixture: the expanded log says the count once, in order, never 'You answered: Kept'", () => {
  const s = propsRun(true);
  const text = logText(s, true);
  assert.deepEqual(text, [
    "prop_master: Find the things in the story",
    "You looked over the props: kept 2",
    "director: Block the scene",
  ]);
  const all = text.join("\n");
  assert.equal(all.match(/\b2\b/g).length, 1, "the props count appears once");
  assert.ok(!/You answered: Kept/i.test(all));
  assert.ok(!/Kept 2 props/i.test(all));
});

test("props fixture after a reload: the step keeps its summary, the answer line is neutral", () => {
  const s = propsRun(false);
  assert.equal(s.pending, null);
  const text = logText(s, true);
  assert.deepEqual(text, [
    "prop_master: Kept 2 props.",
    "You looked over the props earlier.",
    "director: Block the scene",
  ]);
  assert.equal(text.join("\n").match(/\b2\b/g).length, 1);
});

test("stepDetail: task while running, summary when done, all closed when the run is done", () => {
  const s = run(ev("subagent_start", { name: "director", task: "Block it" }));
  assert.equal(stepDetail(s, s.steps[0]), "Block it");
  const e = runReducer(s, ev("subagent_end", { name: "director", summary: "Blocked" }));
  assert.equal(stepDetail(e, e.steps[0]), "Blocked");
  const d = runReducer(s, ev("done", { memoryId: "m" }));
  assert.equal(stepDetail(d, d.steps[0]), "Block it", "done without a summary keeps the task");
});

// ---------------------------------------------------------------------------
// errorRecovery: Retry for a dropped connection, "start over" for the run's own error

test("errorRecovery: nothing to recover unless the run has failed", () => {
  for (const phase of ["connecting", "running", "waiting", "scene_ready", "done"]) {
    assert.equal(errorRecovery({ phase, errorFrom: null }), null, phase);
    assert.equal(errorRecovery({ phase, errorFrom: "run" }), null, phase);
  }
});

test("errorRecovery: the run's own error starts over, a connection error reconnects", () => {
  assert.equal(errorRecovery({ phase: "error", errorFrom: "run" }), "start_over");
  assert.equal(errorRecovery({ phase: "error", errorFrom: "connection" }), "reconnect");
  assert.equal(errorRecovery({ phase: "error", errorFrom: null }), "reconnect", "unknown origin falls back to reconnect");
});

test("errorRecovery on reducer states: error event, give-up, and a wake after the error", () => {
  const failed = run(ev("plan", { todos: [] }), ev("error", { message: "The studio stopped." }));
  assert.equal(errorRecovery(failed), "start_over");
  const dropped = run(ev("plan", { todos: [] }), { type: "connection_error", message: "Lost" });
  assert.equal(errorRecovery(dropped), "reconnect");
  const woke = runReducer(failed, ev("subagent_start", { name: "director" }));
  assert.equal(errorRecovery(woke), null, "a run that wakes shows no recovery");
});

test("startOverNote: mentions sending again only when that is offered", () => {
  assert.equal(startOverNote(true), "Your story is kept, so you can send it again.");
  assert.equal(startOverNote(false), "Nothing was added to the books this time.");
  // The error message above it already says the studio stopped: never repeat it.
  for (const note of [startOverNote(true), startOverNote(false)]) assert.doesNotMatch(note, /couldn’t finish/);
});

test("phaseLine: a dropped stream says it is reconnecting, then the phase line comes back", () => {
  const paused = run(ev("question", { text: "Who?" }));
  const dropped = runReducer(paused, { type: "reconnecting" });
  assert.equal(phaseLine(dropped), RECONNECTING_LINE);
  assert.equal(phaseLine({ ...initialRunState, reconnecting: true }), RECONNECTING_LINE);
  const back = runReducer(dropped, ev("plan", { todos: [] }));
  assert.notEqual(phaseLine(back), RECONNECTING_LINE);
});

test("phaseLine: none on error (the error box says it)", () => {
  const failed = run(ev("error", { message: "The studio stopped." }));
  assert.equal(phaseLine(failed), null);
  assert.equal(phaseLine({ ...failed, reconnecting: true }), null);
});
