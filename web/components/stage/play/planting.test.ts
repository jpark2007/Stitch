// Run with: cd web && node --test "components/stage/play/*.test.ts"
// Node strips the TypeScript; modules are loaded by URL because their
// specifiers need the .ts extension, which tsc rejects in a static import.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import type * as Planting from "./planting";
import type * as Kinematics from "../puppet/kinematics";
import type { RigDef } from "../puppet/kinematics";
import type { Timeline } from "@/lib/types";

const { FootPlanter, travelStretches, travelWeight } = (await import(
  new URL("./planting.ts", import.meta.url).href
)) as typeof Planting;
const { createFrames, forwardKinematics, lowestFoot } = (await import(
  new URL("../puppet/kinematics.ts", import.meta.url).href
)) as typeof Kinematics;

const readJson = <T>(rel: string): T => JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8")) as T;
const rig = readJson<RigDef>("../../../../shared/rig/puppet.json");
const poses = readJson<{ poses: Record<string, Record<string, number | number[]>>; cycles: Record<string, { frames: Record<string, number>[] }> }>(
  "../../../../shared/rig/poses.json",
);
const hymn = readJson<Timeline>("../../../public/samples/hymn_books.timeline.json");

const getter = (rot: Record<string, number>) => (bone: string) => rot[bone] ?? 0;
const walking = [
  [0, [0, 0, 0.5]],
  [2, [1, 0, 0.5]],
] as const;

test("the drop matches the lab's lowestFoot formula on every walk and run frame", () => {
  const planter = new FootPlanter(rig, walking);
  const frames = createFrames(rig);
  for (const name of ["walk", "run"]) {
    for (const f of poses.cycles[name].frames) {
      const rot: Record<string, number> = {};
      for (const [k, v] of Object.entries(f)) if (k !== "phase") rot[k] = v;
      forwardKinematics(rig, rot, frames);
      const want = (rig.hipHeight - lowestFoot(rig, frames)) * 0.5;
      assert.ok(Math.abs(planter.drop(1, getter(rot), 0.5) - want) < 1e-9, `${name} @ ${f.phase}`);
    }
  }
});

test("standing never drops", () => {
  const planter = new FootPlanter(rig, walking);
  assert.equal(planter.drop(1, () => 0, 0.5), 0);
});

test("sit and kneel are not lowered again (their rootOffset is baked in)", () => {
  const still = [[0, [0, -0.12, 0.5]], [3, [0, -0.12, 0.5]]] as const;
  const planter = new FootPlanter(rig, still);
  for (const name of ["sit", "kneel"]) {
    const rot: Record<string, number> = {};
    for (const [k, v] of Object.entries(poses.poses[name])) if (typeof v === "number") rot[k] = v;
    assert.equal(planter.drop(1, getter(rot), 0.5), 0, name);
  }
  assert.equal(planter.idle, true);
});

test("travel stretches merge consecutive moving segments and ease at their ends", () => {
  const s = travelStretches([
    [0, [-1, 0, 0.5]],
    [1, [-1, 0, 0.5]],
    [2, [0, 0, 0.5]],
    [3, [0.5, 0, 0.5]],
    [4, [0.5, 0, 0.5]],
  ]);
  assert.deepEqual([...s.bounds], [1, 3]);
  assert.equal(travelWeight(s, 0.5), 0);
  assert.equal(travelWeight(s, 1), 0);
  assert.equal(travelWeight(s, 2), 1);
  assert.equal(travelWeight(s, 3), 0);
  assert.equal(travelWeight(s, 3.5), 0);
  assert.ok(travelWeight(s, 1.04) > 0 && travelWeight(s, 1.04) < 1);
});

test("hymn_books: Dad's two walks are the only planted stretches", () => {
  const track = hymn.tracks.find((t) => t.target === "puppet:dad" && t.channel === "root.position");
  assert.ok(track);
  const s = travelStretches(track.keys as [number, unknown][]);
  assert.deepEqual([...s.bounds], [1.2, 3.2, 4.8, 6.514]);
  const mom = hymn.tracks.find((t) => t.target === "puppet:mom" && t.channel === "root.position");
  assert.ok(mom);
  assert.equal(new FootPlanter(rig, mom.keys as [number, unknown][]).idle, true);
});
