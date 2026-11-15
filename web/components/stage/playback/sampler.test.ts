// Run with: cd web && node --test "components/stage/playback/*.test.ts"
// Node strips the TypeScript; sampler.ts is loaded by URL because its import
// specifier needs the .ts extension, which tsc rejects in a static import.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import type { Timeline } from "@/lib/types";
import type * as Sampler from "./sampler";
import type { PlaybackFrame } from "./types";

const { compileTimeline, sampleTimeline, parseParent, targetMatches, boneRotation, partRotation, partOffset } = (await import(
  new URL("./sampler.ts", import.meta.url).href
)) as typeof Sampler;

const readJson = <T>(rel: string): T => JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8")) as T;

const hymn = readJson<Timeline>("../../../public/samples/hymn_books.timeline.json");
const rig = readJson<{ bones: Record<string, unknown> }>("../../../../shared/rig/puppet.json");
const BONES = Object.keys(rig.bones);

const compiled = compileTimeline(hymn, { bones: BONES });
const frame = compiled.createFrame();
const at = (t: number, options?: Parameters<typeof compiled.sample>[2]): PlaybackFrame =>
  compiled.sample(t, frame, options);

function close(actual: number, expected: number, msg?: string) {
  assert.ok(Math.abs(actual - expected) < 1e-6, `${msg ?? ""} expected ${expected}, got ${actual}`);
}
function closeVec(actual: readonly number[], expected: readonly number[], msg?: string) {
  assert.equal(actual.length, expected.length, msg);
  expected.forEach((e, i) => close(actual[i], e, `${msg ?? ""}[${i}]`));
}

const puppet = (id: string) => frame.puppetsById.get(id)!;
const bone = (id: string, name: string) => boneRotation(puppet(id), name);
const books = () => frame.propsById.get("hymn_books")!;
const layer = (n: number) => frame.layersByTarget.get(`set:church_steps_1988.layer${n}`)!;

// ------------------------------------------------------------------ layout

test("frame lists every puppet, prop and set layer in the timeline", () => {
  assert.deepEqual(
    frame.puppets.map((p) => p.id),
    ["dad", "mom"],
  );
  assert.deepEqual(
    frame.props.map((p) => p.id),
    ["hymn_books"],
  );
  assert.deepEqual(
    frame.layers.map((l) => [l.setId, l.layer]),
    [
      ["church_steps_1988", 0],
      ["church_steps_1988", 1],
      ["church_steps_1988", 2],
    ],
  );
  assert.equal(frame.duration, 10.65);
  assert.deepEqual(compiled.issues, []);
});

test("every puppet carries every rig bone", () => {
  for (const p of frame.puppets) {
    assert.deepEqual(p.bones, BONES, "rig bones, in rig order, and no extras");
    assert.equal(p.rotations.length, BONES.length);
    assert.equal(p.boneIndex.get("arm_r.upper"), BONES.indexOf("arm_r.upper"));
  }
  assert.equal(boneRotation(frame.puppets[0], "tail"), 0, "unknown bones read as 0");
});

// ------------------------------------------------------------------ before the first key

test("before the first key every track holds its first value", () => {
  at(-1);
  closeVec(puppet("dad").position, [-1.3, 0, 0.5]);
  closeVec(puppet("mom").position, [0.2, 0.3, 0.5]);
  assert.equal(puppet("dad").facing, 1);
  assert.equal(puppet("mom").facing, -1);
  // Dad's arm tracks start at 1.2 with 0; mom holds the books from t=0.
  close(bone("dad", "arm_r.upper"), 0);
  close(bone("mom", "arm_r.upper"), 0.3);
  close(bone("mom", "arm_r.lower"), 1.2);
  assert.equal(books().parent.raw, "puppet:mom.hand_r");
  closeVec(books().position, [0, 0, 0]);
  closeVec(frame.camera.position, [0, 1.1, 3.4]);
  closeVec(frame.camera.lookAt, [0, 0.2, 0.5]);
  close(frame.camera.fov, 35);
  close(layer(0).fold, 0);
  close(layer(2).fold, 0);
});

test("t = 0 is the start of the fly-in with every layer flat", () => {
  at(0);
  close(layer(0).fold, 0);
  close(layer(1).fold, 0);
  close(layer(2).fold, 0);
  closeVec(frame.camera.position, [0, 1.1, 3.4]);
  assert.equal(frame.narration.caption, null);
  assert.equal(frame.shot?.id, "s1");
});

test("bones with no track are 0", () => {
  at(5);
  close(bone("mom", "arm_l.upper"), 0);
  close(bone("mom", "torso"), 0);
  close(bone("dad", "head"), 0);
});

// ------------------------------------------------------------------ between keys

test("set layers rise linearly, staggered back to front", () => {
  at(0.4);
  close(layer(0).fold, 0.5); // 0 -> 1 over 0 .. 0.8
  close(layer(1).fold, 0.25); // 0 -> 1 over 0.2 .. 1.0
  close(layer(2).fold, 0); // first key at 0.4
  at(1.2);
  close(layer(0).fold, 1);
  close(layer(1).fold, 1);
  close(layer(2).fold, 1);
});

test("root.position and bone rotations interpolate linearly", () => {
  at(2.2);
  closeVec(puppet("dad").position, [-0.95, 0, 0.5]); // -1.3 -> -0.6 over 1.2 .. 3.2
  at(9.1);
  close(bone("mom", "head"), -0.2); // 0 -> -0.4 over 8.9 .. 9.3
});

test("camera position and lookAt are eased; fov is linear", () => {
  // Segment 0 .. 1.2 from y=1.1 to y=0.6. Smoothstep at u=0.25 is 0.15625.
  at(0.3);
  close(frame.camera.position[1], 1.1 - 0.5 * 0.15625);
  close(frame.camera.position[2], 3.4 - 0.8 * 0.15625);
  close(frame.camera.lookAt[1], 0.2 + 0.15 * 0.15625);
  at(0.6); // u = 0.5 is the same eased or not
  closeVec(frame.camera.position, [0, 0.85, 3.0]);

  const t: Timeline = {
    duration: 2,
    tracks: [{ target: "camera", channel: "fov", keys: [[0, 30], [2, 40]] }],
  };
  const f = sampleTimeline(t, 0.5);
  close(f.camera.fov, 32.5);
});

test("an empty camera falls back to the wide pose", () => {
  const f = sampleTimeline({ duration: 1, tracks: [] }, 0.5);
  closeVec(f.camera.position, [0, 0.6, 2.6]);
  closeVec(f.camera.lookAt, [0, 0.35, 0.5]);
  close(f.camera.fov, 35);
});

// ------------------------------------------------------------------ parent swaps

test("the drop: parent swaps to ground at 4.45 and position steps, then falls linearly", () => {
  at(4.449);
  assert.equal(books().parent.raw, "puppet:mom.hand_r");
  closeVec(books().position, [0, 0, 0], "still an offset from mom's hand, not blending to the ground");
  at(4.45);
  assert.equal(books().parent.kind, "ground");
  closeVec(books().position, [0.045, 0.513, 0.5]);
  at(4.6);
  closeVec(books().position, [0.0825, 0.4065, 0.5]);
  at(4.75);
  closeVec(books().position, [0.12, 0.3, 0.5]);
});

test("on the ground the books hold still until dad picks them up at 7.1", () => {
  at(6.0);
  assert.equal(books().parent.kind, "ground");
  closeVec(books().position, [0.12, 0.3, 0.5], "never interpolated toward the hand offset");
  at(7.099);
  closeVec(books().position, [0.12, 0.3, 0.5]);
  at(7.1);
  const p = books().parent;
  assert.equal(p.raw, "puppet:dad.hand_r");
  assert.equal(p.kind, "puppet");
  assert.equal(p.id, "dad");
  assert.equal(p.socket, "hand_r");
  assert.equal(p.target, "puppet:dad");
  closeVec(books().position, [0, 0, 0]);
});

test("hand_to: dad hands the books back to mom at 8.3", () => {
  at(8.0);
  assert.equal(books().parent.raw, "puppet:dad.hand_r");
  at(8.3);
  assert.equal(books().parent.raw, "puppet:mom.hand_r");
  closeVec(books().position, [0, 0, 0]);
});

test("parent refs are shared objects, not reallocated per frame", () => {
  const a = at(8.5).props[0].parent;
  const b = at(9.5).props[0].parent;
  assert.equal(a, b);
});

// ------------------------------------------------------------------ after the last key

test("after the last key every track holds its last value", () => {
  at(100);
  closeVec(puppet("dad").position, [0, 0, 0.5]);
  close(bone("mom", "head"), -0.4);
  close(bone("mom", "arm_r.upper"), 0.3);
  close(bone("dad", "arm_r.upper"), 0);
  assert.equal(books().parent.raw, "puppet:mom.hand_r");
  closeVec(books().position, [0, 0, 0]);
  closeVec(frame.camera.position, [0.2, 0.6, 1.5]);
  closeVec(frame.camera.lookAt, [0.2, 0.55, 0.5]);
  close(layer(0).fold, 1);
});

// ------------------------------------------------------------------ step vs linear

const stepTimeline: Timeline = {
  duration: 4,
  tracks: [
    { target: "puppet:p", channel: "root.facing", keys: [[0, 1], [2, -1]] },
    { target: "puppet:p", channel: "root.position", keys: [[0, [0, 0, 0.5]], [2, [1, 0, 0.5]]] },
    { target: "prop:box", channel: "state", keys: [[0, "closed"], [2, "open"]] },
    { target: "prop:box", channel: "rotation", keys: [[0, 0], [2, 1]] },
    { target: "prop:box", channel: "part.lid.rot", keys: [[1, 0], [3, 2]] },
    { target: "prop:box", channel: "parent", keys: [[0, "ground"], [2, "prop:table.top"]] },
    { target: "prop:box", channel: "position", keys: [[0, [0, 0, 0.5]], [1, [0.5, 0, 0.5]], [2, [0, 0.1, 0]], [3, [0, 0.2, 0]]] },
  ],
};

test("step channels jump on their key; linear channels blend", () => {
  const c = compileTimeline(stepTimeline);
  const f = c.createFrame();
  const p = f.puppetsById.get("p")!;
  const box = f.propsById.get("box")!;

  c.sample(1.999, f);
  assert.equal(p.facing, 1);
  assert.equal(box.state, "closed");
  close(p.position[0], 0.9995);
  close(box.rotation, 0.9995);
  close(partRotation(box, "lid"), 0.999, "part rot is linear"); // 0 -> 2 over 1 .. 3
  assert.equal(box.parent.raw, "ground");
  closeVec(box.position, [0.5, 0, 0.5], "holds across the segment into a parent key");

  c.sample(2, f);
  assert.equal(p.facing, -1);
  assert.equal(box.state, "open");
  assert.equal(box.parent.kind, "prop");
  assert.equal(box.parent.socket, "top");
  closeVec(box.position, [0, 0.1, 0]);

  c.sample(2.5, f);
  closeVec(box.position, [0, 0.15, 0], "interpolates within one parent");
  close(partRotation(box, "lid"), 1.5);
});

test("position steps at a parent key even when a segment starts before it", () => {
  const c = compileTimeline(stepTimeline);
  const f = c.createFrame();
  c.sample(0.5, f);
  closeVec(f.props[0].position, [0.25, 0, 0.5], "first segment has no parent change: linear");
});

test("findKey stays correct when sampling jumps around (hint invalidation)", () => {
  const times = [9.5, 0.1, 7.2, 4.6, 4.45, 4.75, 8.3, 7.1, 0, 10.65, 3, 6];
  const reference = compileTimeline(hymn, { bones: BONES });
  for (const t of times) {
    const fresh = reference.sample(t, reference.createFrame());
    at(t);
    closeVec(books().position, fresh.props[0].position, `t=${t}`);
    assert.equal(books().parent.raw, fresh.props[0].parent.raw, `t=${t}`);
    closeVec(puppet("dad").position, fresh.puppetsById.get("dad")!.position, `t=${t}`);
  }
});

test("unsorted keys are sorted and malformed keys are skipped with an issue", () => {
  const c = compileTimeline({
    duration: 2,
    tracks: [
      { target: "puppet:a", channel: "head.rot", keys: [[2, 1], [0, 0], [1, "oops"]] },
      { target: "puppet:a", channel: "mystery", keys: [[0, 1]] },
      { target: "prop:b", channel: "parent", keys: [[0, "nowhere"]] },
    ],
  });
  const f = c.sample(1, c.createFrame());
  close(boneRotation(f.puppets[0], "head"), 0.5);
  assert.deepEqual(f.puppets[0].bones, ["head"], "without a rig, only tracked bones");
  assert.equal(c.issues.length, 2);
  assert.match(c.issues.join("\n"), /mystery: unknown channel/);
  assert.match(c.issues.join("\n"), /bad parent "nowhere"/);
  assert.equal(f.props[0].parent.kind, "ground");
});

// ------------------------------------------------------------------ narration

test("narrators: the default teller first, then the others", () => {
  assert.deepEqual(
    compiled.narrators.map((n) => n.id),
    ["dad", "mom"],
  );
  assert.equal(compiled.defaultNarrator, "dad");
  assert.equal(compiled.narrator("mom")?.audio, null);
  assert.equal(compiled.narratorIndex("nobody"), 0, "unknown teller falls back to the default");
});

/** Caption text at t for a teller (a fresh read, so TS does not narrow across samples). */
const captionAt = (t: number, narrator?: string): string | null =>
  at(t, narrator ? { narrator } : undefined).narration.caption?.text ?? null;

test("captions follow the chosen teller over the same action", () => {
  assert.equal(captionAt(1.0, "mom"), null, "nothing before the first line");

  assert.equal(captionAt(3.3), "I saw her at the top of the church steps…");
  assert.equal(frame.narration.narrator, "dad");

  assert.equal(captionAt(3.3, "mom"), "And of course they went everywhere, right in front of him.");
  assert.equal(frame.narration.narrator, "mom");
  // The action is shared: the scene is identical whoever tells it.
  const dadForMom = [...puppet("dad").position];
  closeVec(at(3.3, { narrator: "dad" }).puppetsById.get("dad")!.position, dadForMom);

  assert.equal(captionAt(9, "mom"), "He handed them back so carefully. I couldn't look up.");
  assert.equal(captionAt(9, "nobody"), "I handed them back, and she wouldn't even look at me.", "unknown teller: default");
  at(100, { narrator: "mom" });
  assert.equal(frame.narration.captionIndex, 2, "the last caption holds at the end");
});

test("word index tracks the spoken word inside the caption's line", () => {
  at(1.5);
  assert.equal(frame.narration.line?.words?.[frame.narration.wordIndex].word, "saw");
  at(1.2);
  assert.equal(frame.narration.wordIndex, 0);
});

test("top-level captions are used when there are no per-teller narrations", () => {
  const c = compileTimeline({
    duration: 5,
    tracks: [],
    captions: [{ t: 1, text: "hello" }],
    defaultNarrator: "gran",
  });
  assert.deepEqual(
    c.narrators.map((n) => n.id),
    ["gran"],
  );
  assert.equal(c.sample(2, c.createFrame()).narration.caption?.text, "hello");
  const none = compileTimeline({ duration: 1, tracks: [] });
  assert.equal(none.narrators.length, 0);
  assert.equal(none.sample(0.5, none.createFrame()).narration.narrator, null);
});

// ------------------------------------------------------------------ interactions

test("the pull tab scrubs its targets while the rest of the scene holds", () => {
  const drive = compiled.interaction("drop_books")!;
  assert.equal(drive.type, "scrub");
  assert.equal(drive.from, 4.45);
  assert.equal(drive.to, 4.75);
  close(compiled.scrubTime(drive, 0.5), 4.6);

  at(2.0, { interactions: [{ id: "drop_books", value: 0.5 }] });
  // Targets at 4.6
  assert.equal(books().parent.kind, "ground");
  closeVec(books().position, [0.0825, 0.4065, 0.5]);
  close(bone("mom", "arm_r.upper"), 0.15);
  // Everything else at 2.0
  closeVec(puppet("dad").position, [-1.02, 0, 0.5]);
  closeVec(frame.camera.position, [0, 0.6, 2.6]);
  close(frame.t, 2.0);

  at(2.0, { interactions: [{ id: "drop_books", value: 0 }] });
  closeVec(books().position, [0.045, 0.513, 0.5]);
  at(2.0, { interactions: [{ id: "drop_books", value: 7 }] });
  closeVec(books().position, [0.12, 0.3, 0.5], "travel clamps to 1");

  at(2.0);
  assert.equal(books().parent.raw, "puppet:mom.hand_r", "releasing the tab returns the targets to scene time");
  close(bone("mom", "arm_r.upper"), 0.3);
});

test("unknown interaction ids are ignored", () => {
  at(2.0, { interactions: [{ id: "nope", value: 1 }] });
  assert.equal(books().parent.raw, "puppet:mom.hand_r");
});

test("scrub targets match whole ids, not string prefixes", () => {
  assert.equal(targetMatches("puppet:mom", "puppet:mom"), true);
  assert.equal(targetMatches("set:church_steps_1988", "set:church_steps_1988.layer1"), true);
  assert.equal(targetMatches("puppet:mo", "puppet:mom"), false);
  assert.equal(targetMatches("puppet:mom", "puppet:momo"), false);
});

test("prop_state and prop_part drives override the prop while held", () => {
  const c = compileTimeline({
    duration: 2,
    tracks: [{ target: "prop:car", channel: "part.wheel.rot", keys: [[0, 0], [2, 4]] }],
    interactions: [
      { id: "door", kind: "slider", label: "", at: [0, 0], drives: { type: "prop_state", prop: "car", states: ["closed", "open"] } },
      { id: "spin", kind: "slider", label: "", at: [0, 0], drives: { type: "prop_part", prop: "car", part: "wheel", channel: "rot", range: [0, 10] } },
      { id: "lift", kind: "slider", label: "", at: [0, 0], drives: { type: "prop_part", prop: "car", part: "hood", channel: "offset", range: [0, 0.2] } },
    ],
  });
  const f = c.createFrame();
  const car = f.propsById.get("car")!;
  c.sample(1, f, { interactions: [{ id: "door", value: 0.8 }, { id: "spin", value: 0.5 }, { id: "lift", value: 1 }] });
  assert.equal(car.state, "open");
  close(partRotation(car, "wheel"), 5);
  close(partOffset(car, "hood"), 0.2);
  assert.deepEqual(car.parts, ["wheel", "hood"]);
  c.sample(1, f);
  assert.equal(car.state, null);
  close(partRotation(car, "wheel"), 2, "back to its track");
  close(partOffset(car, "hood"), 0);
});

// ------------------------------------------------------------------ effects and shots

test("effects are active for their duration; time of day holds and blends", () => {
  const c = compileTimeline({
    duration: 10,
    tracks: [],
    effects: [
      { t: 1, kind: "confetti", dur: 2 },
      { t: 4, kind: "time_of_day", dur: 1, value: "dusk" },
      { t: 8, kind: "time_of_day", dur: 2, value: "night" },
    ],
  });
  const f = c.createFrame();
  c.sample(0.5, f);
  assert.equal(f.activeEffects, 0);
  assert.equal(f.timeOfDay, null);
  c.sample(2, f);
  assert.equal(f.effects[0].active, true);
  close(f.effects[0].progress, 0.5);
  assert.equal(f.activeEffects, 1);
  c.sample(4.5, f);
  assert.equal(f.effects[0].active, false);
  assert.equal(f.effects[0].progress, 1);
  assert.equal(f.timeOfDay, "dusk");
  assert.equal(f.timeOfDayFrom, null);
  close(f.timeOfDayMix, 0.5);
  c.sample(9, f);
  assert.equal(f.timeOfDay, "night");
  assert.equal(f.timeOfDayFrom, "dusk");
  close(f.timeOfDayMix, 0.5);
});

test("a scrub tab owns the effects in its window, never time of day", () => {
  const timeline: Timeline = {
    duration: 12,
    tracks: [],
    effects: [
      { t: 0, kind: "steam", dur: 12 },
      { t: 2, kind: "confetti", dur: 2 },
      { t: 3.5, kind: "smoke", dur: 1 },
      { t: 3, kind: "time_of_day", dur: 1, value: "dusk" },
      { t: 5, kind: "rain", dur: 1 },
      { t: 5.0000001, kind: "confetti" },
      { t: 9, kind: "confetti", dur: 2 },
    ],
    interactions: [
      { id: "hug", kind: "pull_tab", label: "Pull", at: [0, 0], drives: { type: "scrub", from: 2, to: 5, targets: ["puppet:mom"] } },
      { id: "box", kind: "pull_tab", label: "Pull", at: [0, 0], drives: { type: "prop_state", prop: "box", states: ["a", "b"] } },
      { id: "all", kind: "slider", label: "Slide", at: [0, 0], drives: { type: "scrub" } },
    ],
  };
  const c = compileTimeline(timeline);
  assert.deepEqual(c.interaction("hug")!.effects, [1, 2, 4, 5], "both ends count; steam before and confetti after do not");
  assert.deepEqual(c.interaction("box")!.effects, [], "only a scrub replays effects");
  assert.deepEqual(c.interaction("all")!.effects, [0, 1, 2, 4, 5, 6], "a scrub of the whole scene owns every effect but time of day");
});

test("an effect clock replays an effect on its own time; NaN follows the scene", () => {
  const c = compileTimeline({
    duration: 10,
    tracks: [],
    effects: [
      { t: 2, kind: "confetti", dur: 2 },
      { t: 6, kind: "smoke", dur: 1 },
    ],
  });
  const f = c.createFrame();
  c.sample(8, f);
  assert.equal(f.effects[0].replaying, false);
  assert.equal(f.effects[0].time, 8, "scene time when nothing replays it");
  assert.equal(f.effects[0].progress, 1);
  assert.equal(f.activeEffects, 0);

  const clock = new Float64Array([3, Number.NaN]);
  c.sample(8, f, { effectClock: clock });
  assert.equal(f.t, 8, "the scene stays where it is");
  assert.equal(f.effects[0].replaying, true);
  assert.equal(f.effects[0].time, 3);
  assert.equal(f.effects[0].active, true);
  close(f.effects[0].progress, 0.5);
  assert.equal(f.effects[1].replaying, false);
  assert.equal(f.effects[1].time, 8);
  assert.equal(f.effects[1].progress, 1);
  assert.equal(f.activeEffects, 1);

  c.sample(1, f, { effectClock: new Float64Array([1.5]) });
  assert.equal(f.effects[0].active, false, "a clock before the start is before the start");
  assert.equal(f.effects[0].progress, 0);
  assert.equal(f.effects[1].time, 1, "a short clock leaves the rest on scene time");

  c.sample(2.5, f, { effectClock: new Float64Array([Number.NaN, Number.NaN]) });
  assert.equal(f.effects[0].replaying, false);
  close(f.effects[0].progress, 0.25, "back on scene time");
});

test("parseParent handles every parent form", () => {
  assert.equal(parseParent("ground")?.kind, "ground");
  assert.deepEqual({ ...parseParent("prop:sedan.driver") }, {
    raw: "prop:sedan.driver",
    kind: "prop",
    id: "sedan",
    socket: "driver",
    target: "prop:sedan",
  });
  assert.equal(parseParent("puppet:dad"), null);
  assert.equal(parseParent("hand_r"), null);
});

test("sampling allocates nothing measurable over many frames", () => {
  const f = compiled.createFrame();
  const options = {
    narrator: "mom",
    interactions: [{ id: "drop_books", value: 0.3 }],
    effectClock: new Float64Array((hymn.effects ?? []).length + 1).fill(Number.NaN),
  };
  // Warm up so the JIT settles, then measure heap growth across 50k samples.
  for (let i = 0; i < 20000; i++) compiled.sample((i % 1065) / 100, f, options);
  const before = process.memoryUsage().heapUsed;
  for (let i = 0; i < 50000; i++) compiled.sample((i % 1065) / 100, f, options);
  const grown = process.memoryUsage().heapUsed - before;
  // The one unavoidable heap object is the boxed `t` argument (16 bytes) the
  // caller passes in; a single extra object or array per binding would be ~1 KB.
  assert.ok(grown / 50000 < 24, `heap grew ${grown} bytes (${(grown / 50000).toFixed(1)} per sample)`);
});
