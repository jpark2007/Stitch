// Run with: cd web && node --test "components/stage/play/*.test.ts"
// What the timeline stage binds for the reference sample (web/public/samples/
// hymn_books.*), and what it reports when the scene data is missing pieces.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import type { MemoryResponse, Timeline } from "@/lib/types";
import type * as Cast from "./cast";
import type * as Sampler from "../playback/sampler";
import type { SceneData } from "./scene";

const { castScene, personLookup, placeholderProp } = (await import(new URL("./cast.ts", import.meta.url).href)) as typeof Cast;
const { compileTimeline } = (await import(new URL("../playback/sampler.ts", import.meta.url).href)) as typeof Sampler;

const readJson = <T>(rel: string): T => JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8")) as T;
const timeline = readJson<Timeline>("../../../public/samples/hymn_books.timeline.json");
const memory = readJson<MemoryResponse>("../../../public/samples/hymn_books.memory.json");
const rig = readJson<{ bones: Record<string, unknown> }>("../../../../shared/rig/puppet.json");
const BONES = Object.keys(rig.bones);

const scene: SceneData = {
  id: memory.memory.id,
  title: memory.memory.title,
  year: memory.memory.year ?? null,
  familyId: null,
  timeline,
  sets: memory.sets ?? [],
  props: memory.props ?? [],
  people: memory.people ?? [],
};
const compiled = compileTimeline(timeline, { bones: BONES });
const frame = compiled.createFrame();

test("hymn_books binds both tellers, the hymn books and the steps' three layers, with no issues", () => {
  const cast = castScene(scene, frame);
  assert.deepEqual(cast.issues, []);
  assert.deepEqual(
    cast.puppets.map((p) => [p.id, p.index, p.known]),
    [
      ["dad", 0, true],
      ["mom", 1, true],
    ],
  );
  assert.equal(cast.puppets[0].person.name, scene.people.find((p) => p.id === "dad")!.name);
  assert.deepEqual(
    cast.props.map((p) => [p.prop.id, p.placeholder]),
    [["hymn_books", false]],
  );
  assert.equal(cast.sets.length, 1);
  const steps = cast.sets[0];
  assert.equal(steps.set.id, "church_steps_1988");
  assert.deepEqual([...steps.layers].sort(), [0, 1, 2]);
  // Back first: depths never decrease along `layers`.
  const depths = steps.layers.map((i) => steps.set.layers[i].depth);
  assert.deepEqual(depths, [...depths].sort((a, b) => a - b));
  // Each fold is the frame's own slot, so the bindings read it in place.
  for (const lf of steps.folds) assert.equal(frame.layersByTarget.get(lf.target), lf);
});

test("every puppet bone the rig names has a rotation slot", () => {
  for (const p of castScene(scene, frame).puppets) {
    const pf = frame.puppetsById.get(p.id)!;
    for (const bone of BONES) assert.ok(pf.boneIndex.has(bone), `${p.id}.${bone}`);
  }
});

test("the hymn books pass mom -> ground -> dad -> mom, held in the right hand", () => {
  const pf = frame.propsById.get("hymn_books")!;
  const parentAt = (t: number) => compiled.sample(t, frame).propsById.get("hymn_books")!.parent;
  assert.equal(parentAt(1).raw, "puppet:mom.hand_r");
  assert.equal(parentAt(5).kind, "ground");
  const dad = parentAt(7.5);
  assert.deepEqual([dad.kind, dad.id, dad.socket, dad.target], ["puppet", "dad", "hand_r", "puppet:dad"]);
  assert.equal(parentAt(9).raw, "puppet:mom.hand_r");
  // The same frame slot throughout: a swap is a pointer change, not a rebind.
  assert.equal(frame.propsById.get("hymn_books"), pf);
});

test("missing people, props and sets still play, and are reported", () => {
  const thin: SceneData = {
    ...scene,
    people: scene.people.filter((p) => p.id !== "mom"),
    props: [],
    sets: [],
  };
  const cast = castScene(thin, frame);
  const mom = cast.puppets.find((p) => p.id === "mom")!;
  assert.equal(mom.known, false);
  assert.equal(mom.person.name, "mom");
  assert.match(mom.person.character.color, /^#[0-9a-f]{6}$/);
  assert.deepEqual(cast.props, [{ prop: placeholderProp("hymn_books"), placeholder: true }]);
  assert.deepEqual(cast.sets, []);
  const said = cast.issues.join("\n");
  assert.match(said, /puppet:mom: no person "mom"/);
  assert.match(said, /prop:hymn_books: no prop "hymn_books"/);
  assert.match(said, /set:church_steps_1988\.layer0: no set "church_steps_1988"/);
  assert.match(said, /shot s1: no set "church_steps_1988"/);
  assert.ok(!said.includes("puppet:dad"), "dad is described");
});

test("a set with fewer layers than its tracks binds the ones it has", () => {
  const short = scene.sets.map((s) => ({ ...s, layers: s.layers.slice(0, 2) }));
  const cast = castScene({ ...scene, sets: short }, frame);
  assert.deepEqual([...cast.sets[0].layers].sort(), [0, 1]);
  assert.deepEqual(cast.issues, ['set:church_steps_1988.layer2: set "church_steps_1988" has 2 layer(s); not drawn']);
});

test("a stand-in sheet is the same object every time for the same id", () => {
  const lookup = personLookup({ familyId: null, people: [] });
  assert.equal(lookup("ghost"), lookup("ghost"));
  assert.notEqual(lookup("ghost").character.color, lookup("other").character.color);
});
