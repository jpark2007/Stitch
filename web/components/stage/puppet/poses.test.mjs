import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { applyPose, compileCycle, cyclePhase, dampRotations, poseRootOffset, sampleCycle } from "./poses.ts";

const read = (p) => JSON.parse(readFileSync(new URL(`../../../../shared/${p}`, import.meta.url), "utf8"));
const rig = read("rig/puppet.json");
const lib = read("rig/poses.json");
const bones = Object.keys(rig.bones);
const close = (a, b, eps = 1e-9, msg = "") => assert.ok(Math.abs(a - b) <= eps, `${msg} ${a} != ${b}`);

test("every pose names only rig bones (plus rootOffset)", () => {
  for (const [name, pose] of Object.entries(lib.poses)) {
    for (const k of Object.keys(pose)) {
      if (k === "rootOffset") continue;
      assert.ok(bones.includes(k), `${name}: ${k}`);
    }
  }
});

test("applyPose fills every bone and zeros the unlisted ones", () => {
  const out = { torso: 5, "arm_r.upper": 5 };
  applyPose(lib.poses.bow_head, bones, out);
  assert.equal(out.head, -0.4);
  assert.equal(out.torso, 0);
  assert.equal(out["arm_r.upper"], 0);
  assert.equal(Object.keys(out).length, bones.length);
  assert.equal("rootOffset" in out, false);
});

test("applyPose of a missing pose is standing", () => {
  const out = applyPose(undefined, bones, {});
  for (const b of bones) assert.equal(out[b], 0);
});

test("poseRootOffset", () => {
  assert.deepEqual(poseRootOffset(lib.poses.sit), [0, -0.24]);
  assert.deepEqual(poseRootOffset(lib.poses.wave), [0, 0]);
});

test("sampleCycle hits each frame exactly at its phase (smooth and linear)", () => {
  for (const name of ["walk", "run"]) {
    const c = compileCycle(lib.cycles[name], bones);
    for (const f of lib.cycles[name].frames) {
      for (const smooth of [true, false]) {
        const out = sampleCycle(c, f.phase, {}, smooth);
        for (const b of bones) close(out[b], f[b] ?? 0, 1e-9, `${name}@${f.phase} ${b}`);
      }
    }
  }
});

test("linear sampling is the midpoint halfway between frames", () => {
  const c = compileCycle(lib.cycles.walk, bones);
  const [f0, f1] = lib.cycles.walk.frames;
  const out = sampleCycle(c, (f0.phase + f1.phase) / 2, {}, false);
  close(out["leg_l.upper"], (f0["leg_l.upper"] + f1["leg_l.upper"]) / 2);
});

test("sampling wraps: phase 1.3 and -0.7 equal 0.3; last segment blends into the first", () => {
  const c = compileCycle(lib.cycles.walk, bones);
  const a = sampleCycle(c, 0.3, {});
  const b = sampleCycle(c, 1.3, {});
  const d = sampleCycle(c, -0.7, {});
  for (const k of bones) {
    close(a[k], b[k], 1e-9);
    close(a[k], d[k], 1e-9);
  }
  const last = lib.cycles.walk.frames[3];
  const first = lib.cycles.walk.frames[0];
  const mid = sampleCycle(c, 0.875, {}, false);
  close(mid["leg_l.upper"], (last["leg_l.upper"] + first["leg_l.upper"]) / 2);
});

test("smooth sampling is continuous across the wrap", () => {
  const c = compileCycle(lib.cycles.walk, bones);
  const a = sampleCycle(c, 0.99999, {});
  const b = sampleCycle(c, 0.00001, {});
  for (const k of bones) close(a[k], b[k], 1e-3, k);
});

test("the walk cycle alternates legs half a cycle apart", () => {
  const c = compileCycle(lib.cycles.walk, bones);
  for (const p of [0, 0.1, 0.33, 0.6]) {
    const a = sampleCycle(c, p, {});
    const b = sampleCycle(c, p + 0.5, {});
    close(a["leg_l.upper"], b["leg_r.upper"], 1e-9);
  }
});

test("cyclePhase: one stride × scale is one full cycle", () => {
  close(cyclePhase(0.4, 0.8, 0.5), 0);
  close(cyclePhase(0.1, 0.8, 0.5), 0.25);
});

test("dampRotations eases toward the target and stops there at k = 1", () => {
  const cur = { torso: 0 };
  dampRotations(cur, { torso: 1 }, ["torso"], 0.25);
  close(cur.torso, 0.25);
  dampRotations(cur, { torso: 1 }, ["torso"], 1);
  close(cur.torso, 1);
});
