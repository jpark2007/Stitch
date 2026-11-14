// Run with: cd web && node --test "components/stage/props/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in rig.ts; no build step needed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { Matrix4, Quaternion, Vector3 } from "three";

import {
  allArtIds,
  artFor,
  bodyArtKey,
  createPropPose,
  fractionOr,
  groundMatrix,
  heldMatrix,
  imageToLocal,
  parseParent,
  propDims,
  propSocketMatrix,
  rollAngle,
  socketPivot,
} from "../rig.ts";

const SIZES = { tiny: 0.03, hand: 0.08, carried: 0.2, furniture: 0.4, vehicle: 0.9, building: 1.5 };
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
const nearVec = (v, [x, y, z], eps = 1e-9) => {
  near(v.x, x, eps);
  near(v.y, y, eps);
  near(v.z, z, eps);
};
/** Where image fraction (u, v) lands in stage space under matrix m. */
const landing = (m, u, v, d) => {
  const p = imageToLocal({ x: 0, y: 0 }, u, v, d.width, d.height);
  return new Vector3(p.x, p.y, 0).applyMatrix4(m);
};
const zRot = (a) => new Quaternion().setFromAxisAngle(new Vector3(0, 0, 1), a);

test("height comes from the size class and width from the aspect", () => {
  assert.deepEqual(propDims({ size: "hand", aspect: 1 }, SIZES), { height: 0.08, width: 0.08, aspect: 1 });
  near(propDims({ size: "vehicle", aspect: 2 }, SIZES).width, 1.8);
  near(propDims({ size: "tiny" }, SIZES, 0.5).width, 0.015);
  assert.equal(propDims({ size: "tiny", aspect: -3 }, SIZES).aspect, 1);
});

test("image fractions map to a centred, y-up local frame", () => {
  assert.deepEqual(imageToLocal({ x: 0, y: 0 }, 0, 0, 2, 1), { x: -1, y: 0.5 });
  assert.deepEqual(imageToLocal({ x: 0, y: 0 }, 0.5, 1, 2, 1), { x: 0, y: -0.5 });
  assert.deepEqual(fractionOr(null, [0.5, 1]), [0.5, 1]);
  assert.deepEqual(fractionOr([0.2], [0.5, 1]), [0.5, 1]);
  assert.deepEqual(fractionOr([0.2, 0.3], [0.5, 1]), [0.2, 0.3]);
});

test("on the ground the anchor sits on the position, even when rotated", () => {
  const d = propDims({ size: "hand", aspect: 1.5 }, SIZES);
  const a = imageToLocal({ x: 0, y: 0 }, 0.5, 1, d.width, d.height);
  for (const rot of [0, 0.7, -2]) {
    const m = groundMatrix(new Matrix4(), [0.12, 0.3, 0.5], rot, a.x, a.y);
    nearVec(landing(m, 0.5, 1, d), [0.12, 0.3, 0.5]);
  }
  // Unrotated, the top centre is one height straight above the anchor.
  const m = groundMatrix(new Matrix4(), [0, 0, 0.5], 0, a.x, a.y);
  nearVec(landing(m, 0.5, 0, d), [0, d.height, 0.5]);
});

test("held, the grip sits on the socket and the prop keeps its world size", () => {
  const d = propDims({ size: "carried", aspect: 1 }, SIZES);
  const g = imageToLocal({ x: 0, y: 0 }, 0.5, 0, d.width, d.height);
  // A wrist inside a puppet drawn at half scale, turned by its forearm.
  const socket = new Matrix4().compose(new Vector3(0.3, 0.4, 0.5), zRot(1.1), new Vector3(0.5, 0.5, 0.5));

  const upright = heldMatrix(new Matrix4(), socket, "upright", [0, 0, 0], 0, g.x, g.y);
  nearVec(landing(upright, 0.5, 0, d), [0.3, 0.4, 0.5]);
  // Upright: the bottom hangs straight down, a full world height below.
  nearVec(landing(upright, 0.5, 1, d), [0.3, 0.4 - d.height, 0.5]);

  const turned = heldMatrix(new Matrix4(), socket, "socket", [0, 0, 0], 0, g.x, g.y);
  nearVec(landing(turned, 0.5, 0, d), [0.3, 0.4, 0.5]);
  const bottom = landing(turned, 0.5, 1, d);
  near(bottom.distanceTo(new Vector3(0.3, 0.4, 0.5)), d.height);
  near(bottom.x, 0.3 + Math.sin(1.1) * d.height);
});

test("a held prop is not mirrored by a puppet facing left", () => {
  // The compiler's anchor_from_grip (api/screenplay/body.py) puts the anchor
  // at grip + (anchor.u - grip.u) * width whatever the facing; the stage must
  // agree, or the prop jumps at the drop key.
  const prop = { size: "carried", aspect: 1.5 };
  const grip = [0.2, 0.5];
  const anchor = [0.5, 1];
  const d = propDims(prop, SIZES);
  const g = imageToLocal({ x: 0, y: 0 }, grip[0], grip[1], d.width, d.height);
  const hand = [0.3, 0.5, 0.5];
  const compilerAnchor = [
    hand[0] + (anchor[0] - grip[0]) * d.width,
    hand[1] + (grip[1] - anchor[1]) * d.height,
    0.5,
  ];
  for (const facing of [1, -1]) {
    // A puppet's hand socket: mirrored when facing left, drawn at 0.6 scale.
    const socket = new Matrix4().compose(new Vector3(...hand), new Quaternion(), new Vector3(0.6 * facing, 0.6, 0.6));
    const m = heldMatrix(new Matrix4(), socket, "upright", [0, 0, 0], 0, g.x, g.y);
    nearVec(landing(m, grip[0], grip[1], d), hand);
    nearVec(landing(m, anchor[0], anchor[1], d), compilerAnchor);
    assert.ok(m.determinant() > 0, `facing ${facing}: drawn mirrored`);
    // In socket orientation it turns with the socket but still never mirrors.
    const turned = heldMatrix(new Matrix4(), socket, "socket", [0, 0, 0], 0, g.x, g.y);
    nearVec(landing(turned, grip[0], grip[1], d), hand);
    assert.ok(turned.determinant() > 0, `facing ${facing}, socket: drawn mirrored`);
  }
});

test("a hand socket takes the grip; the head and prop sockets take the anchor", () => {
  assert.equal(socketPivot(parseParent("puppet:dad.hand_r")), "grip");
  assert.equal(socketPivot(parseParent("puppet:mom.hand_l")), "grip");
  assert.equal(socketPivot(parseParent("puppet:dad.head")), "anchor");
  assert.equal(socketPivot(parseParent("prop:car.roof")), "anchor");
  assert.equal(socketPivot(parseParent("prop:hook.hang"), "grip"), "grip");
  // The override is for prop sockets only: a puppet's sockets follow the compiler.
  assert.equal(socketPivot(parseParent("puppet:dad.head"), "grip"), "anchor");
  assert.equal(socketPivot(parseParent("puppet:dad.hand_r"), "anchor"), "grip");
  assert.equal(socketPivot(parseParent("ground")), "anchor");
});

test("put on: a worn cap rests its anchor on the head, with no jump from the hand", () => {
  // Mirrors api/tests/screenplay/test_body.py test_put_on_self: put_on_keys
  // reaches the hand to grip_from_anchor(cap, head), then parents the cap to
  // puppet:dad.head at [0, 0, 0]; at that key anchor_from_grip(cap, hand) is
  // the head socket. Also an off-centre grip, which would jump sideways.
  const head = [0.12, 0.62, 0.5];
  const caps = [
    { prop: { size: "hand", aspect: 1.5, anchor: [0.5, 1], grip: [0.5, 0.5] } },
    { prop: { size: "hand", aspect: 1.5, anchor: [0.5, 1], grip: [0.15, 0.3] } },
  ];
  for (const { prop } of caps) {
    const d = propDims(prop, SIZES);
    const a = imageToLocal({ x: 0, y: 0 }, prop.anchor[0], prop.anchor[1], d.width, d.height);
    const g = imageToLocal({ x: 0, y: 0 }, prop.grip[0], prop.grip[1], d.width, d.height);
    // body.py grip_from_anchor: where the hand is at the parent key.
    const hand = [
      head[0] - (prop.anchor[0] - prop.grip[0]) * d.width,
      head[1] - (prop.grip[1] - prop.anchor[1]) * d.height,
      head[2],
    ];
    for (const facing of [1, -1]) {
      const scale = new Vector3(0.6 * facing, 0.6, 0.6);
      const headSocket = new Matrix4().compose(new Vector3(...head), new Quaternion(), scale);
      const handSocket = new Matrix4().compose(new Vector3(...hand), new Quaternion(), scale);

      const wornParent = parseParent("puppet:dad.head");
      assert.equal(socketPivot(wornParent), "anchor");
      const worn = heldMatrix(new Matrix4(), headSocket, "upright", [0, 0, 0], 0, a.x, a.y);
      // The anchor (the cap's bottom edge) is on the head socket.
      nearVec(landing(worn, prop.anchor[0], prop.anchor[1], d), head);

      // Just before the key, in the hand by its grip: the very same placement.
      assert.equal(socketPivot(parseParent("puppet:dad.hand_r")), "grip");
      const held = heldMatrix(new Matrix4(), handSocket, "upright", [0, 0, 0], 0, g.x, g.y);
      for (let i = 0; i < 16; i++) near(held.elements[i], worn.elements[i]);
    }
  }
});

test("the held offset is in socket space and rotation pivots on the grip", () => {
  const d = propDims({ size: "hand", aspect: 1 }, SIZES);
  const g = imageToLocal({ x: 0, y: 0 }, 0.5, 0, d.width, d.height);
  const socket = new Matrix4().makeTranslation(0.1, 0.2, 0.5);
  const m = heldMatrix(new Matrix4(), socket, "upright", [0.01, -0.02, 0], 0.9, g.x, g.y);
  nearVec(landing(m, 0.5, 0, d), [0.11, 0.18, 0.5]);
});

test("a socket on a prop moves with the prop", () => {
  const d = propDims({ size: "vehicle", aspect: 2 }, SIZES);
  const a = imageToLocal({ x: 0, y: 0 }, 0.5, 1, d.width, d.height);
  const car = groundMatrix(new Matrix4(), [0.4, 0, 0.5], 0, a.x, a.y);
  const seat = imageToLocal({ x: 0, y: 0 }, 0.35, 0.55, d.width, d.height);
  const s = propSocketMatrix(new Matrix4(), car, seat.x, seat.y);
  nearVec(new Vector3().setFromMatrixPosition(s), [0.4 + (0.35 - 0.5) * d.width, 0.45 * d.height, 0.5]);
});

test("parents parse into kind, target, id and socket", () => {
  assert.deepEqual(parseParent("ground"), { kind: "ground", target: "", id: "", socket: "" });
  assert.deepEqual(parseParent("puppet:mom.hand_r"), { kind: "puppet", target: "puppet:mom", id: "mom", socket: "hand_r" });
  assert.deepEqual(parseParent("prop:car.driver"), { kind: "prop", target: "prop:car", id: "car", socket: "driver" });
  assert.equal(parseParent("table").kind, "invalid");
  assert.equal(parseParent("puppet:mom").kind, "invalid");
  // Cached: same object for the same string.
  assert.equal(parseParent("puppet:dad.hand_r"), parseParent("puppet:dad.hand_r"));
});

test("art: body key, state overrides and preload list", () => {
  const car = {
    art: { body: "media/a1", wheel: "media/a2" },
    parts: { wheel_front: { art: "wheel", at: [0.22, 0.9], spin: true } },
    states: { parked: {}, wrecked: { body: "media/a3" } },
  };
  assert.equal(bodyArtKey(car), "body");
  assert.equal(bodyArtKey({ art: { wheel: "w", shell: "s" }, parts: { w: { art: "wheel", at: [0, 0] } } }), "shell");
  assert.equal(bodyArtKey({ art: {}, parts: {} }), null);
  assert.equal(artFor(car, "body", null), "media/a1");
  assert.equal(artFor(car, "body", "parked"), "media/a1");
  assert.equal(artFor(car, "body", "wrecked"), "media/a3");
  assert.equal(artFor(car, "wheel", "wrecked"), "media/a2");
  assert.equal(artFor({ art: { body: null } }, "body", null), null);
  assert.deepEqual(allArtIds(car).sort(), ["media/a1", "media/a2", "media/a3"]);
});

test("poses copy their inputs and wheels roll clockwise going right", () => {
  const pos = [1, 2, 3];
  const p = createPropPose({ position: pos });
  pos[0] = 9;
  assert.deepEqual(p.position, [1, 2, 3]);
  assert.equal(p.parent, "ground");
  near(rollAngle(Math.PI * 0.2, 0.1), -2 * Math.PI);
  assert.equal(rollAngle(1, 0), 0);
});
