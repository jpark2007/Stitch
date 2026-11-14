// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in crafting.ts and runState.ts. Run
// states are built with the real run reducer from event sequences like the
// studio's, so the scene is checked against what the feed itself sees.
import { test } from "node:test";
import assert from "node:assert/strict";

import { initialRunState, runReducer } from "../../agent/runState.ts";
import {
  ENTRANCE,
  MAX_FRAMES,
  SETTLED,
  SET_LAYERS,
  applyPropEdits,
  bookOf,
  castOf,
  clip,
  craftScene,
  createWatch,
  entranceProgress,
  holdScene,
  inkSteps,
  inkedLines,
  namesLine,
  openingLines,
  pageTitle,
  pageYear,
  planStage,
  plainSummary,
  propColor,
  propsFromChips,
  readCast,
  sceneKeys,
  scheduleEntrances,
  sentences,
  shotCount,
  tapProps,
  taskLine,
} from "../crafting.ts";

const ev = (event, data) => ({ type: "event", message: { event, data } });
const run = (...actions) => actions.reduce(runReducer, initialRunState);
const start = (name, task = null) => ev("subagent_start", { name, task });
const end = (name, summary = null) => ev("subagent_end", { name, summary });
const plan = (...items) => ev("plan", { todos: items.map(([text, status]) => ({ text, status })) });
const CHIPS = [
  { id: "hymn", name: "hymn books", size: "hand" },
  { id: "car", name: "green sedan", size: "vehicle" },
];

// ---------------------------------------------------------------- text

test("sentences: split on stops and line breaks, keep closing quotes", () => {
  assert.deepEqual(sentences("We sang. Then the books fell!  Was it 1988?"), [
    "We sang.",
    "Then the books fell!",
    "Was it 1988?",
  ]);
  assert.deepEqual(sentences('She said "hi." He waved\nthe end'), ['She said "hi."', "He waved", "the end"]);
  assert.deepEqual(sentences("   "), []);
});

test("clip: cuts at a word with an ellipsis, leaves short text alone", () => {
  assert.equal(clip("a short line", 40), "a short line");
  assert.equal(clip("one two three four five six", 14), "one two three…");
  assert.equal(clip("  spaced   out  ", 40), "spaced out");
});

test("openingLines: the first sentences that fit, else the first clipped", () => {
  assert.equal(openingLines("One. Two. Three."), "One. Two.");
  assert.equal(openingLines(""), "");
  const long = "x".repeat(250);
  assert.equal(openingLines(long).length <= 201, true);
  assert.equal(openingLines(`Short. ${"y ".repeat(120)}`), "Short.");
});

test("inkedLines / inkSteps: inked sentence by sentence, never past the page's room", () => {
  const text = "We sang. The books fell. Mom laughed.";
  assert.equal(inkSteps(text), 3);
  assert.equal(inkedLines(text, 1), "We sang.");
  assert.equal(inkedLines(text, 2), "We sang. The books fell.");
  assert.equal(inkedLines(text, Infinity), text);
  assert.equal(inkedLines(text, 0), "We sang.");
  const long = Array.from({ length: 30 }, (_, i) => `Sentence number ${i} is here.`).join(" ");
  const steps = inkSteps(long);
  assert.ok(steps > 1 && steps < 30);
  assert.ok(inkedLines(long, Infinity).length <= 361);
  assert.ok(inkedLines(long, Infinity).endsWith("…"));
});

test("pageYear: a whole year only", () => {
  assert.equal(pageYear("1988"), 1988);
  assert.equal(pageYear(" 1975 "), 1975);
  assert.equal(pageYear(""), null);
  assert.equal(pageYear("19"), null);
  assert.equal(pageYear("198a"), null);
  assert.equal(pageYear(2001), 2001);
});

test("pageTitle: the moment, else a short question, else a new memory", () => {
  assert.equal(pageTitle({ secondTellingOf: "The wedding", question: "How?" }), "The wedding");
  assert.equal(pageTitle({ question: "How did you meet Mom?" }), "How did you meet Mom?");
  assert.equal(pageTitle({ question: "x".repeat(80) }), "A new memory");
  assert.equal(pageTitle({}), "A new memory");
});

test("namesLine and bookOf", () => {
  assert.equal(namesLine(["Ana"]), "Ana");
  assert.equal(namesLine(["Ana", "David"]), "Ana and David");
  assert.equal(namesLine(["Ana", "David", "Rosa"]), "Ana, David and Rosa");
  assert.equal(namesLine(["Ana", "David", "Rosa", "Luis"]), "Ana, David and 2 others");
  assert.equal(bookOf("Ana Rivera"), "Ana’s book");
  assert.equal(bookOf(""), "Your book");
});

test("shotCount: digits or words before 'shot(s)'", () => {
  assert.equal(shotCount("Three shots: arriving, the moment, and after."), 3);
  assert.equal(shotCount("2 shots"), 2);
  assert.equal(shotCount("One shot."), 1);
  assert.equal(shotCount("A storyboard."), null);
  assert.equal(shotCount(null), null);
});

test("taskLine: short tasks under way; long or odd ones are left to the stage line", () => {
  assert.equal(taskLine("Find the props in the telling"), "Finding the props in the telling…");
  assert.equal(taskLine("Stage the scene on the church steps"), "Staging the scene on the church steps…");
  assert.equal(taskLine("Read the telling and find what is missing"), "Reading the telling…");
  assert.equal(taskLine("Time Ana's words to the action"), "Timing Ana's words to the action…");
  assert.equal(taskLine("Fix the hand-off."), "Fixing the hand-off…");
  assert.equal(taskLine("Find or build the place"), null, "a verb alone says nothing");
  assert.equal(taskLine("Produce a shot list for: the story"), null);
  assert.equal(taskLine("Ponder the meaning of it all"), null);
  assert.equal(taskLine(`Find ${"very ".repeat(20)}long things`), null);
  assert.equal(taskLine(null), null);
});

test("plainSummary: short and plain only", () => {
  assert.equal(plainSummary("Reused the church steps."), "Reused the church steps.");
  assert.equal(plainSummary("1 shot, 9.2s, compiled without errors."), null);
  assert.equal(plainSummary("shot s2, t=4.5: cannot hand_to"), null);
  assert.equal(plainSummary("x".repeat(100)), null);
});

test("planStage: plan words to stages", () => {
  assert.equal(planStage("Read the telling"), "reading");
  assert.equal(planStage("Ask one follow-up"), "reading");
  assert.equal(planStage("Find the props"), "props");
  assert.equal(planStage("Stage the scene"), "staging");
  assert.equal(planStage("Narrate"), "narration");
  assert.equal(planStage("Write the narration"), "narration");
  assert.equal(planStage("Ask the others for their version"), "linking");
  assert.equal(planStage("Something else"), null);
});

// ---------------------------------------------------------------- props

test("propColor is stable and from the palette; propsFromChips keeps earlier edits", () => {
  assert.equal(propColor("hymn books"), propColor("Hymn Books"));
  const first = propsFromChips(CHIPS);
  assert.deepEqual(first.map((p) => [p.id, p.name, p.removed]), [
    ["hymn", "hymn books", false],
    ["car", "green sedan", false],
  ]);
  const edited = applyPropEdits(first, [
    { id: "hymn", action: "rename", name: "Grandma's hymnal" },
    { id: "car", action: "remove" },
  ]);
  assert.equal(edited[0].name, "Grandma's hymnal");
  assert.equal(edited[1].removed, true);
  // A replayed props_found (a reconnect) keeps what the family changed.
  const replayed = propsFromChips(CHIPS, edited);
  assert.equal(replayed[0].name, "Grandma's hymnal");
  assert.equal(replayed[1].removed, true);
});

test("tapProps: hears props_found and the review, passes everything through", async () => {
  const messages = [
    { event: "plan", data: { todos: [] } },
    { event: "props_found", data: { props: CHIPS } },
    { event: "done", data: { memoryId: "m1" } },
  ];
  const sent = [];
  const source = {
    async *stream() {
      yield* messages;
    },
    answer: async (body) => {
      sent.push(body);
    },
  };
  const heard = [];
  const edits = [];
  const tapped = tapProps(source, (chips) => heard.push(chips), (e) => edits.push(e));
  const seen = [];
  for await (const m of tapped.stream(new AbortController().signal)) seen.push(m);
  assert.deepEqual(seen, messages);
  assert.deepEqual(heard, [CHIPS]);
  await tapped.answer({ kind: "answer", text: "hi" });
  await tapped.answer({ kind: "props", props: [{ id: "hymn", action: "keep" }] });
  assert.equal(sent.length, 2);
  assert.deepEqual(edits, [[{ id: "hymn", action: "keep" }]]);
});

test("tapProps: a failed review is not applied", async () => {
  const source = {
    async *stream() {},
    answer: async () => {
      throw new Error("nope");
    },
  };
  const edits = [];
  const tapped = tapProps(source, () => {}, (e) => edits.push(e));
  await assert.rejects(tapped.answer({ kind: "props", props: [{ id: "x", action: "remove" }] }));
  assert.deepEqual(edits, []);
});

// ---------------------------------------------------------------- the scene

test("craftScene: nothing stands before the run says so", () => {
  const s = craftScene(initialRunState);
  assert.equal(s.stage, "opening");
  assert.equal(s.frames, 0);
  assert.equal(s.layers, 0);
  assert.equal(s.cast, false);
  assert.equal(s.ink, 0);
  assert.deepEqual(s.props, []);
  assert.match(s.caption, /new page/);
});

test("craftScene: reading, then a question waits with the book", () => {
  const reading = craftScene(run(plan(["Read the telling", "in_progress"]), start("interviewer", "Read the telling")));
  assert.equal(reading.stage, "reading");
  assert.equal(reading.caption, "Reading your story…");
  const asked = craftScene(
    run(plan(["Read the telling", "in_progress"]), start("interviewer"), end("interviewer", "One follow-up."), ev("question", { text: "Who?" })),
  );
  assert.equal(asked.waiting, "answer");
  assert.equal(asked.note, "Waiting for your answer.");
  assert.doesNotMatch(asked.caption, /Who\?/);
});

test("craftScene: the storyboard sketches one frame while it works, the named shots once done", () => {
  const working = craftScene(run(start("storyboarder", "Break the story into shots")));
  assert.equal(working.stage, "storyboard");
  assert.equal(working.frames, 1);
  assert.equal(working.sketching, true);
  assert.equal(working.caption, "Sketching the storyboard…");
  const done = craftScene(run(start("storyboarder"), end("storyboarder", "Three shots: arriving, the moment, and after.")));
  assert.equal(done.frames, 3);
  assert.equal(done.sketching, false);
  assert.equal(done.caption, "Three shots: arriving, the moment, and after.");
  const many = craftScene(run(start("storyboarder"), end("storyboarder", "Nine shots.")));
  assert.equal(many.frames, MAX_FRAMES);
});

test("craftScene: props appear only once found, with their names in the caption", () => {
  const looking = craftScene(run(start("prop_master", "Find the things in the story")), []);
  assert.deepEqual(looking.props, []);
  assert.equal(looking.caption, "Finding the things in the story…");
  const props = propsFromChips(CHIPS);
  const found = craftScene(run(start("prop_master"), ev("props_found", { props: CHIPS })), props);
  assert.equal(found.waiting, "props");
  assert.equal(found.props.length, 2);
  assert.equal(found.caption, "Cutting out the hymn books and the green sedan…");
  const one = craftScene(run(start("prop_master")), applyPropEdits(props, [{ id: "car", action: "remove" }]));
  assert.equal(one.caption, "Cutting out the hymn books…");
});

test("craftScene: the set rises a layer while it is built, all of it once done", () => {
  assert.equal(craftScene(run(start("set_designer"))).layers, 1);
  const built = craftScene(run(start("set_designer"), end("set_designer", "Reused the church steps.")));
  assert.equal(built.layers, SET_LAYERS);
  assert.equal(built.caption, "Reused the church steps.");
});

test("craftScene: staging names the cast; a fix nudges instead of starting over", () => {
  const staging = craftScene(run(start("director", "Block the scene")), [], { cast: ["Ana", "David"] });
  assert.equal(staging.cast, true);
  assert.equal(staging.caption, "Putting Ana and David on their marks…");
  const fixing = craftScene(
    run(
      start("director", "Block the scene"),
      end("director", "First pass."),
      ev("compile_error", { errors: [{ code: "not_holding", message: "x" }], round: 1 }),
      start("director", "Fix the hand-off"),
    ),
    [],
    { cast: ["Ana"] },
  );
  assert.equal(fixing.fixes, 1);
  assert.equal(fixing.caption, "Fixing the hand-off…");
  assert.equal(fixing.cast, true);
  const sentBack = craftScene(run(start("director"), end("director"), start("checker"), end("checker", "Dad is hidden."), start("director")));
  assert.equal(sentBack.fixes, 1);
});

test("craftScene: narration from the narrator, or from the plan when there is none", () => {
  const n = craftScene(run(start("narrator")), [], { teller: "Ana" });
  assert.equal(n.ink, 1);
  assert.equal(n.caption, "Inking Ana’s words…");
  assert.equal(craftScene(run(start("narrator"), end("narrator"))).ink, 2);
  // The scripted studio has no narrator sub-agent: its plan says Narrate.
  const scripted = craftScene(run(start("director"), end("director", "ok"), plan(["Stage the scene", "completed"], ["Narrate", "in_progress"])));
  assert.equal(scripted.ink, 1);
  assert.equal(scripted.stage, "narration");
});

test("craftScene: the checker's frames are counted and its critique is the caption", () => {
  const s = craftScene(
    run(start("checker"), ev("frames", { frames: [{ t: 1, mediaId: "a" }, { t: 2, mediaId: "b" }], critique: "Everyone is on their marks." })),
  );
  assert.equal(s.stage, "checking");
  assert.equal(s.checked, 2);
  assert.equal(s.caption, "Everyone is on their marks.");
});

test("craftScene: every plan item done reads as the last touches; scene_ready and done after", () => {
  const all = run(plan(["Stage the scene", "completed"], ["Narrate", "completed"]));
  assert.equal(craftScene(all).stage, "finishing");
  const ready = runReducer(all, ev("scene_ready", { memoryId: "m1", version: 1 }));
  assert.equal(craftScene(ready).stage, "ready");
  assert.equal(craftScene(ready).memoryId, "m1");
  const done = runReducer(ready, ev("done", { memoryId: "m1" }));
  assert.equal(craftScene(done).stage, "done");
  assert.equal(craftScene(done).caption, "Your page is made.");
  assert.equal(craftScene(done, [], { second: true }).caption, "Your version is on the page.");
});

test("craftScene: an error leaves the page unfinished and says so", () => {
  const s = craftScene(run(start("director"), ev("error", { message: "The studio stopped." })));
  assert.equal(s.stage, "stopped");
  assert.equal(s.cast, true, "what was made stays on the page");
  assert.match(s.note, /Your story is kept/);
  const lost = craftScene(runReducer(run(start("director")), { type: "connection_error", message: "gone" }));
  assert.equal(lost.stage, "stopped");
  assert.match(lost.note, /lost touch/);
});

test("craftScene: the mock studio's whole run, in order", () => {
  // The event order of components/flow/mock/mockBackend.ts runScript.
  const props = propsFromChips(CHIPS.slice(0, 1));
  const steps = [
    plan(["Read the telling", "in_progress"]),
    start("interviewer", "Read the telling and find what is missing"),
    end("interviewer", "One follow-up: who else was there."),
    ev("question", { text: "Who else was there that day?" }),
    { type: "answered", kind: "answer", summary: "Just us" },
    start("storyboarder", "Break the story into shots"),
    end("storyboarder", "Three shots: arriving, the moment, and after."),
    start("prop_master", "Find the things in the story"),
    ev("props_found", { props: CHIPS.slice(0, 1) }),
    { type: "answered", kind: "props", summary: "No changes" },
    end("prop_master", "Kept 1 prop."),
    start("set_designer", "Find or build the place"),
    end("set_designer", "Reused the church steps."),
    start("director", "Block the scene"),
    end("director", "First pass at the blocking."),
    ev("compile_error", { errors: [{ code: "not_holding", message: "x" }], round: 1 }),
    start("director", "Fix the hand-off"),
    end("director", "Picked it up first."),
    start("narrator", "Time Ana's words to the action"),
    end("narrator", "Narration recorded and cued."),
    start("checker", "Look at the scene at key moments"),
    ev("frames", { frames: [{ t: 1, mediaId: "a" }, { t: 2, mediaId: "b" }, { t: 3, mediaId: "c" }], critique: "Reads clearly." }),
    end("checker", "The pages look right."),
    start("linker", "Put it in everyone's books"),
    end("linker", "Added it to 2 books."),
    ev("scene_ready", { memoryId: "m1", version: 1 }),
    ev("done", { memoryId: "m1" }),
  ];
  const stages = [];
  let state = initialRunState;
  for (const a of steps) {
    state = runReducer(state, a);
    const s = craftScene(state, props, { cast: ["Ana"], teller: "Ana" });
    if (stages.at(-1) !== s.stage) stages.push(s.stage);
  }
  assert.deepEqual(stages, [
    "reading",
    "storyboard",
    "props",
    "set",
    "staging",
    "narration",
    "checking",
    "linking",
    "ready",
    "done",
  ]);
  const last = craftScene(state, props);
  assert.equal(last.frames, 3);
  assert.equal(last.layers, SET_LAYERS);
  assert.equal(last.ink, 2);
  assert.equal(last.checked, 3);
  assert.equal(last.linked, true);
  assert.equal(last.fixes, 1);
});

test("holdScene: a reconnect replaying from nothing keeps the page as it was", () => {
  const shown = craftScene(run(start("director")));
  const blank = craftScene(initialRunState);
  assert.equal(holdScene(shown, blank, initialRunState), shown);
  assert.equal(holdScene(null, blank, initialRunState), blank);
  const next = craftScene(run(start("narrator")));
  assert.equal(holdScene(shown, next, run(start("narrator"))), next);
});

// ---------------------------------------------------------------- entrances

test("scheduleEntrances: one after another, never sooner than now", () => {
  const a = scheduleEntrances(new Map(), ["p1"], 1000);
  assert.equal(a.get("p1"), 1000);
  const b = scheduleEntrances(a, ["p1", "p2", "p3"], 1100);
  assert.equal(b.get("p1"), 1000, "a piece keeps its time");
  assert.equal(b.get("p2"), 1000 + ENTRANCE.gap);
  assert.equal(b.get("p3"), 1000 + 2 * ENTRANCE.gap);
  const later = scheduleEntrances(b, ["p1", "p2", "p3", "p4"], 60_000);
  assert.equal(later.get("p4"), 60_000, "a piece arriving minutes later starts at once");
});

test("scheduleEntrances: a burst settles all but the last few (no queue that runs behind)", () => {
  const keys = Array.from({ length: 12 }, (_, i) => `k${i}`);
  const m = scheduleEntrances(new Map(), keys, 5000);
  const settled = keys.filter((k) => m.get(k) === SETTLED);
  assert.equal(settled.length, 12 - ENTRANCE.burst);
  const last = Math.max(...keys.map((k) => m.get(k)));
  assert.ok(last - 5000 <= ENTRANCE.gap * ENTRANCE.burst, "the queue never runs more than a burst behind");
});

test("scheduleEntrances: instant (a resumed run, reduced motion) settles everything; gone pieces drop", () => {
  const m = scheduleEntrances(new Map(), ["a", "b"], 10, { ...ENTRANCE, instant: true });
  assert.deepEqual([...m.values()], [SETTLED, SETTLED]);
  const n = scheduleEntrances(m, ["b"], 20);
  assert.deepEqual([...n.keys()], ["b"]);
});

test("entranceProgress", () => {
  assert.equal(entranceProgress(undefined, 100, 500), 0);
  assert.equal(entranceProgress(SETTLED, 100, 500), 1);
  assert.equal(entranceProgress(100, 350, 500), 0.5);
  assert.equal(entranceProgress(100, 50, 500), 0);
  assert.equal(entranceProgress(100, 900, 500), 1);
});

test("sceneKeys: pieces in the order they arrive", () => {
  const s = craftScene(run(start("storyboarder"), end("storyboarder", "Two shots."), start("prop_master")), propsFromChips(CHIPS));
  assert.deepEqual(sceneKeys(s, 2), ["frame:0", "frame:1", "prop:hymn", "prop:car"]);
  const staged = craftScene(run(start("director")));
  assert.deepEqual(sceneKeys(staged, 2), ["cast:0", "cast:1"]);
  assert.deepEqual(sceneKeys(craftScene(run(start("linker"))), 0), ["ribbon"]);
});

// ---------------------------------------------------------------- the cast

test("castOf: the teller first, each once, known people only, capped", () => {
  const people = [
    { id: "ana", name: "Ana", color: "#123456" },
    { id: "dav", name: "David", color: null },
    { id: "ros", name: "Rosa", color: "#abcdef", item: "red scarf", build: "short" },
  ];
  const cast = castOf(people, "ana", ["dav", "ana", "ghost", "ros"]);
  assert.deepEqual(cast.map((c) => c.id), ["ana", "dav", "ros"]);
  assert.equal(cast[1].color, "#b9573d");
  assert.equal(cast[2].item, "red scarf");
  assert.equal(castOf(people, null, ["ana", "dav", "ros"], 2).length, 2);
});

test("createWatch: get, set, subscribe; the same value tells nobody", () => {
  const w = createWatch(1);
  let heard = 0;
  const off = w.subscribe(() => heard++);
  w.set(1);
  assert.equal(heard, 0);
  w.set(2);
  assert.equal(w.get(), 2);
  assert.equal(heard, 1);
  off();
  w.set(3);
  assert.equal(heard, 1);
});

test("readCast: survives junk", () => {
  assert.deepEqual(readCast(null), []);
  assert.deepEqual(readCast("not json"), []);
  assert.deepEqual(readCast('{"a":1}'), []);
  assert.deepEqual(readCast('[{"id":"a","name":"Ana","color":"#111"},{"id":3}]'), [
    { id: "a", name: "Ana", color: "#111", item: null, build: null },
  ]);
});
