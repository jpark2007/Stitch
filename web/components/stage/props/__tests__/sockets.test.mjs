// Run with: cd web && node --test "components/stage/props/__tests__/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import { Group, Matrix4, Vector3 } from "three";

import { PropRegistry, toStageSpace } from "../sockets.ts";

const at = (m) => new Vector3().setFromMatrixPosition(m).toArray();

test("prop targets resolve from registered props, others from the fallback", () => {
  const puppets = (target, socket, out) => {
    if (target !== "puppet:mom" || socket !== "hand_r") return false;
    out.makeTranslation(1, 2, 3);
    return true;
  };
  const reg = new PropRegistry(puppets);
  reg.register("car", {
    socketMatrix: (socket, out) => socket === "driver" && !!out.makeTranslation(0.4, 0.3, 0.5),
  });
  const out = new Matrix4();
  assert.equal(reg.resolve("prop:car", "driver", out), true);
  assert.deepEqual(at(out), [0.4, 0.3, 0.5]);
  assert.equal(reg.resolve("prop:car", "trunk", out), false);
  assert.equal(reg.resolve("puppet:mom", "hand_r", out), true);
  assert.deepEqual(at(out), [1, 2, 3]);
  assert.equal(reg.resolve("prop:bike", "seat", out), false);
});

test("unregistering only removes the same source", () => {
  const reg = new PropRegistry();
  const a = { socketMatrix: () => true };
  const b = { socketMatrix: () => true };
  const offA = reg.register("x", a);
  reg.register("x", b);
  offA();
  assert.equal(reg.has("x"), true);
});

test("props resting on each other in a loop do not recurse forever", () => {
  const reg = new PropRegistry();
  // Each prop, asked for its socket, first places itself on the other.
  reg.register("a", { socketMatrix: (s, out) => reg.resolve("prop:b", "top", out) });
  reg.register("b", { socketMatrix: (s, out) => reg.resolve("prop:a", "top", out) });
  assert.equal(reg.resolve("prop:a", "top", new Matrix4()), false);
});

test("toStageSpace removes the stage root's transform", () => {
  const root = new Group();
  root.position.set(5, 0, 0);
  root.scale.setScalar(2);
  const hand = new Group();
  hand.position.set(0.1, 0.2, 0.5);
  root.add(hand);
  const m = toStageSpace(hand, root, new Matrix4());
  const p = at(m);
  [0.1, 0.2, 0.5].forEach((v, i) => assert.ok(Math.abs(p[i] - v) < 1e-12));
});
