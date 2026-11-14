import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  boneOrder,
  boneTip,
  createFrames,
  forwardKinematics,
  hipToStage,
  lowestFoot,
  puppetScale,
  socketInHip,
  stageToHip,
} from "./kinematics.ts";

const read = (p) => JSON.parse(readFileSync(new URL(`../../../../shared/${p}`, import.meta.url), "utf8"));
const rig = read("rig/puppet.json");
const vocab = read("vocabulary.json");
const close = (a, b, eps = 1e-9, msg = "") => assert.ok(Math.abs(a - b) <= eps, `${msg} ${a} != ${b}`);

test("boneOrder puts parents first and covers every bone", () => {
  const order = boneOrder(rig);
  assert.equal(order.length, Object.keys(rig.bones).length);
  for (const [i, n] of order.entries()) {
    const p = rig.bones[n].parent;
    if (p !== rig.root) assert.ok(order.indexOf(p) < i, `${p} before ${n}`);
  }
});

test("standing: head socket on top of the head, hands at the wrists", () => {
  const f = forwardKinematics(rig, {}, createFrames(rig));
  const head = socketInHip(rig, f, "head");
  close(head.x, 0);
  close(head.y, 0.3 + 0.22);
  const hr = socketInHip(rig, f, "hand_r");
  close(hr.x, 0.09);
  close(hr.y, 0.28 - 0.34);
  const hl = socketInHip(rig, f, "hand_l");
  close(hl.x, -0.09);
});

test("standing: the feet are exactly hipHeight below the hip", () => {
  const f = forwardKinematics(rig, {}, createFrames(rig));
  close(lowestFoot(rig, f), rig.hipHeight);
  const t = boneTip(rig, f, "leg_r.lower");
  close(t.y, -rig.hipHeight);
});

test("positive rotation swings a down-pointing bone forward (+x)", () => {
  const f = forwardKinematics(rig, { "arm_r.upper": 0.5 }, createFrames(rig));
  const tip = boneTip(rig, f, "arm_r.upper");
  assert.ok(tip.x > 0.09);
});

test("negative rotation leans an up-pointing bone forward (bow_head)", () => {
  const f = forwardKinematics(rig, { head: -0.4 }, createFrames(rig));
  const head = socketInHip(rig, f, "head");
  assert.ok(head.x > 0);
  close(head.x, 0.22 * Math.sin(0.4));
});

test("pivots turn with the parent: a torso lean carries the shoulders", () => {
  const f = forwardKinematics(rig, { torso: -0.3 }, createFrames(rig));
  const [px, py] = rig.bones["arm_r.upper"].pivot;
  close(f["arm_r.upper"].x, px * Math.cos(-0.3) - py * Math.sin(-0.3));
  close(f["arm_r.upper"].y, px * Math.sin(-0.3) + py * Math.cos(-0.3));
  close(f["arm_r.upper"].angle, -0.3);
});

test("stage height follows stageHeight × builds (average 0.5, tall 0.55)", () => {
  close(puppetScale(rig, vocab.builds.average) * rig.height, 0.5);
  close(puppetScale(rig, vocab.builds.tall) * rig.height, 0.55);
});

test("hipToStage: hip is hipHeight × scale above root.position; facing mirrors x", () => {
  const s = puppetScale(rig, 1);
  const p = hipToStage(rig, 0.09, 0, 0.2, 0.3, 0.5, 1, s);
  close(p.x, 0.2 + 0.09 * s);
  close(p.y, 0.3 + rig.hipHeight * s);
  close(p.z, 0.5);
  const m = hipToStage(rig, 0.09, 0, 0.2, 0.3, 0.5, -1, s);
  close(m.x, 0.2 - 0.09 * s);
  const back = stageToHip(rig, m.x, m.y, 0.2, 0.3, -1, s);
  close(back.x, 0.09);
  close(back.y, 0);
});

test("lowestFoot: bent legs lift the feet off the ground", () => {
  const f = forwardKinematics(rig, { "leg_l.upper": 0.45, "leg_l.lower": -0.1, "leg_r.upper": -0.35, "leg_r.lower": -0.25 }, createFrames(rig));
  const drop = lowestFoot(rig, f);
  assert.ok(drop < rig.hipHeight);
  close(drop, 0.24 * Math.cos(0.45) + 0.24 * Math.cos(0.35), 1e-9);
});
