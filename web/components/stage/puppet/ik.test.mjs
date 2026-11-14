// node --test components/stage/puppet/   (from web/)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { solveArmIK, solveTwoBoneIK } from "./ik.ts";
import { boneTip, createFrames, forwardKinematics, socketInHip } from "./kinematics.ts";

const rig = JSON.parse(readFileSync(new URL("../../../../shared/rig/puppet.json", import.meta.url), "utf8"));
const L = rig.bones["arm_r.upper"].length;
const close = (a, b, eps = 1e-9, msg) => assert.ok(Math.abs(a - b) <= eps, `${msg ?? ""} ${a} != ${b}`);

/** Tip of a two-bone chain from the shoulder, for given upper/lower angles. */
function chainTip(upper, lower, len = L) {
  // Bones point down (dir [0, -1]); rotate(dir, a) = (sin a, -cos a).
  return {
    x: len * Math.sin(upper) + len * Math.sin(upper + lower),
    y: -len * Math.cos(upper) - len * Math.cos(upper + lower),
  };
}

test("matches the contract formula exactly", () => {
  for (const [vx, vy] of [
    [0.1, -0.2],
    [0.25, 0.05],
    [-0.12, -0.08],
    [0.0, 0.3],
  ]) {
    const d = Math.hypot(vx, vy);
    const theta = Math.atan2(vx, -vy);
    const a = Math.acos(d / (2 * L));
    const s = solveTwoBoneIK(vx, vy, L);
    close(s.upper, theta - a);
    close(s.lower, 2 * a);
    assert.equal(s.reachable, true);
  }
});

test("arm hanging straight down at full length is all zeros", () => {
  const s = solveTwoBoneIK(0, -2 * L, L);
  close(s.upper, 0);
  close(s.lower, 0);
  assert.equal(s.reachable, true);
});

test("straight forward at full length is a quarter turn, elbow straight", () => {
  const s = solveTwoBoneIK(2 * L, 0, L);
  close(s.upper, Math.PI / 2);
  close(s.lower, 0);
});

test("the chain tip lands on every reachable target", () => {
  for (let i = 0; i < 200; i++) {
    const ang = (i / 200) * Math.PI * 2;
    const r = 2 * L * (0.15 + 0.85 * ((i * 37) % 100) / 100);
    const vx = Math.cos(ang) * r;
    const vy = Math.sin(ang) * r;
    const s = solveTwoBoneIK(vx, vy, L);
    const tip = chainTip(s.upper, s.lower);
    close(tip.x, vx, 1e-9, `x at ${ang}`);
    close(tip.y, vy, 1e-9, `y at ${ang}`);
  }
});

test("elbow bends forward-positive (lower >= 0)", () => {
  const s = solveTwoBoneIK(0.1, -0.1, L);
  assert.ok(s.lower > 0);
});

test("out of reach: flagged, fully extended toward the target", () => {
  const s = solveTwoBoneIK(0.3, 0.3, L);
  assert.equal(s.reachable, false);
  close(s.lower, 0);
  close(s.upper, Math.atan2(0.3, -0.3));
  const tip = chainTip(s.upper, s.lower);
  // Points along v.
  close(Math.atan2(tip.y, tip.x), Math.atan2(0.3, 0.3), 1e-9);
  close(Math.hypot(tip.x, tip.y), 2 * L, 1e-9);
});

test("just at reach is reachable; just past is not", () => {
  assert.equal(solveTwoBoneIK(2 * L, 0, L).reachable, true);
  assert.equal(solveTwoBoneIK(2 * L + 1e-6, 0, L).reachable, false);
});

test("target on the shoulder does not produce NaN", () => {
  const s = solveTwoBoneIK(0, 0, L);
  assert.ok(Number.isFinite(s.upper) && Number.isFinite(s.lower));
});

test("writes into the out object it is given", () => {
  const out = { upper: 9, lower: 9, reachable: false };
  const r = solveTwoBoneIK(0.05, -0.2, L, out);
  assert.equal(r, out);
  assert.equal(out.reachable, true);
});

test("solveArmIK puts the hand socket on a hip-frame target through the real rig", () => {
  const frames = createFrames(rig);
  for (const arm of ["arm_r", "arm_l"]) {
    for (const torso of [0, -0.25, 0.3]) {
      for (const [tx, ty] of [
        [0.25, 0.1],
        [0.15, -0.05],
        [-0.1, 0.35],
        [0.3, 0.3],
      ]) {
        const s = solveArmIK(rig, arm, tx, ty, torso);
        if (!s.reachable) continue;
        const chain = rig.ik[arm];
        const rot = { torso, [chain.upper]: s.upper, [chain.lower]: s.lower };
        forwardKinematics(rig, rot, frames);
        const p = socketInHip(rig, frames, chain.socket);
        close(p.x, tx, 1e-9, `${arm} torso=${torso} x`);
        close(p.y, ty, 1e-9, `${arm} torso=${torso} y`);
      }
    }
  }
});

test("solveArmIK with no torso lean equals the contract on shoulder-relative v", () => {
  const [px, py] = rig.bones["arm_r.upper"].pivot;
  const a = solveArmIK(rig, "arm_r", 0.3, 0.1, 0);
  const b = solveTwoBoneIK(0.3 - px, 0.1 - py, L);
  close(a.upper, b.upper);
  close(a.lower, b.lower);
});

test("hand socket sits at the tip of the lower arm (the wrist)", () => {
  const frames = forwardKinematics(rig, { "arm_r.upper": 0.4, "arm_r.lower": 0.8 }, createFrames(rig));
  const wrist = boneTip(rig, frames, "arm_r.lower");
  const sock = socketInHip(rig, frames, "hand_r");
  close(wrist.x, sock.x);
  close(wrist.y, sock.y);
});

test("unknown arm throws", () => {
  assert.throws(() => solveArmIK(rig, "tail", 0, 0));
});
