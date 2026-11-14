/**
 * The stage's shot (shot.ts): the book's camera follows a playing moment's
 * camera tracks, scaled, and keeps the page's words in frame.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The bug: the moment played in the book without its camera tracks; the
 * book's camera never moved for the timeline's establishing move or its
 * push-in onto a puppet.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Shot = typeof import("./shot");
type Three = typeof import("three");
const paths = ["./shot.ts", "three"];
const s: Shot = await import(paths[0]);
const { Vector3 }: Three = await import(paths[1]);

const DEG = Math.PI / 180;

function pose(p: [number, number, number], t: [number, number, number], fov: number) {
  const out = s.createPose(fov);
  out.position.set(...p);
  out.target.set(...t);
  return out;
}

/** Where `point` lands vertically in the frame of `cam` (-1 bottom .. 1 top). */
function ndcY(cam: ReturnType<typeof pose>, point: InstanceType<Three["Vector3"]>) {
  const f = new Vector3().subVectors(cam.target, cam.position).normalize();
  const up = new Vector3(0, 1, 0).addScaledVector(f, -f.y).normalize();
  const k = new Vector3().subVectors(point, cam.position);
  return k.dot(up) / k.dot(f) / Math.tan((cam.fov * DEG) / 2);
}

// Roughly the book at 1440x900: the reading view of the spread, its view of
// the stage page, and hymn_books' camera mapped into the page.
const book = pose([0, 2.75, 1.77], [0, 0.03, 0.07], 30);
const page = pose([0.52, 2.25, 1.46], [0.52, 0.03, 0.07], 30);
const settled = pose([0.5, 0.32, 0.77], [0.5, 0.23, 0.02], 35);
const pushedIn = pose([0.57, 0.32, 0.38], [0.57, 0.3, 0.02], 35);
const opts = { follow: 0.45, aspect: 1.6, halfWidth: 0.45 };

describe("blendShot", () => {
  it("is the book's own view at weight 0", () => {
    const out = s.blendShot(s.createPose(), book, page, settled, 0, opts);
    assert.deepEqual(out.position.toArray(), book.position.toArray());
    assert.deepEqual(out.target.toArray(), book.target.toArray());
    assert.equal(out.fov, 30);
  });

  it("goes `follow` of the way from the stage page toward the track at weight 1, with the track's lens", () => {
    const out = s.blendShot(s.createPose(), book, page, settled, 1, opts);
    const want = new Vector3().lerpVectors(page.position, settled.position, 0.45);
    assert.ok(out.position.distanceTo(want) < 1e-9);
    assert.ok(Math.abs(out.fov - (30 + 5 * 0.45)) < 1e-9);
    // Lower than the reading view: the set is seen more from the front.
    const elev = (c: typeof out) => Math.atan2(c.position.y - c.target.y, c.position.z - c.target.z);
    assert.ok(elev(out) < elev(book) - 8 * DEG, `elevation ${elev(out) / DEG}`);
  });

  it("lands the track's push-in: closer to the set, and toward where it looks", () => {
    const a = s.blendShot(s.createPose(), book, page, settled, 1, opts);
    const b = s.blendShot(s.createPose(), book, page, pushedIn, 1, opts);
    assert.ok(b.position.distanceTo(b.target) < a.position.distanceTo(a.target) - 0.05);
    assert.ok(b.target.x > a.target.x);
  });

  it("eases in: half weight is half way, and the ends are still", () => {
    const a = s.blendShot(s.createPose(), book, page, settled, 0.5, opts).position.clone();
    const full = s.blendShot(s.createPose(), book, page, settled, 1, opts).position.clone();
    assert.ok(a.distanceTo(new Vector3().lerpVectors(book.position, full, 0.5)) < 1e-9);
    const early = s.blendShot(s.createPose(), book, page, settled, 0.02, opts).position;
    assert.ok(early.distanceTo(book.position) < 0.01 * full.distanceTo(book.position));
  });

  it("never comes so close that the stage's width leaves the frame", () => {
    const narrow = { ...opts, aspect: 390 / 844 };
    const out = s.blendShot(s.createPose(), book, page, pushedIn, 1, narrow);
    const tanH = Math.tan((out.fov * DEG) / 2) * narrow.aspect;
    assert.ok(out.position.distanceTo(out.target) * tanH >= 0.45 - 1e-6);
  });

  it("keeps the words under the set in frame, tilting down to them rather than backing off", () => {
    const keep = new Vector3(0.5, 0.03, 0.47);
    const free = s.blendShot(s.createPose(), book, page, pushedIn, 1, opts);
    assert.ok(ndcY(free, keep) < -0.8, "the push-in alone drops them out of frame");
    const kept = s.blendShot(s.createPose(), book, page, pushedIn, 1, { ...opts, keep, keepBelow: 0.8 });
    assert.ok(Math.abs(ndcY(kept, keep) - -0.8) < 1e-6, `keep at ${ndcY(kept, keep)}`);
    assert.ok(kept.position.distanceTo(free.position) < 1e-9, "the camera stays where the push-in put it");
  });
});

describe("stepShotWeight", () => {
  it("ramps in and out at its own pace, and lands on the first frame", () => {
    assert.equal(s.stepShotWeight(0, 1, 0.5, 2, 1), 0.25);
    assert.equal(s.stepShotWeight(1, 0, 0.5, 2, 1), 0.5);
    assert.equal(s.stepShotWeight(0.9, 1, 0.5, 2, 1), 1);
    assert.equal(s.stepShotWeight(0.3, 0, 0, 2, 1), 0);
  });
});

describe("StageShot.offer", () => {
  // The bug: the book draws on demand and its camera reads `want` before the
  // stage writes it, so a telling's end (offer(false)) with nothing else
  // drawing left the camera leaned in until the pointer moved.
  it("says when the offer changes, so the stage can ask for the frame the camera reads it on", () => {
    const shot = s.createStageShot();
    assert.equal(shot.offer(false), false, "nothing offered yet");
    assert.equal(shot.offer(true), true);
    assert.equal(shot.want, 1);
    assert.equal(shot.offer(true), false, "held: no new frame needed");
    assert.equal(shot.offer(false), true, "given up: the camera must hear of it");
    assert.equal(shot.want, 0);
  });
});
