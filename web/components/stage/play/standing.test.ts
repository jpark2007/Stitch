// Run with: cd web && node --test "components/stage/play/*.test.ts"

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import type * as StandingModule from "./standing";
import type { MemoryResponse } from "@/lib/types";
import type { PlaybackFrame, SetLayerFrame } from "../playback";

const { Standing, foldToRise, risersFor, RISER_THICK } = (await import(new URL("./standing.ts", import.meta.url).href)) as typeof StandingModule;

const memory = JSON.parse(
  readFileSync(new URL("../../../public/samples/hymn_books.memory.json", import.meta.url), "utf8"),
) as MemoryResponse;
const set = memory.sets![0];

/** Just enough of a frame: the set's three layer folds and its shot. */
function frameAt(fold: number) {
  const layers: SetLayerFrame[] = set.layers.map((_, i) => ({ target: `set:${set.id}.layer${i}`, setId: set.id, layer: i, fold }));
  return {
    shot: { id: "s1", set: set.id, start: 0, actionStart: 1.2, end: 10.65 },
    layersByTarget: new Map(layers.map((l) => [l.target, l])),
    layers,
  } as unknown as PlaybackFrame & { layers: SetLayerFrame[] };
}

const m = () => ({ angle: NaN, dy: NaN, dz: NaN });

test("foldToRise: flat at 0, upright at 1, a small pop past upright on the way", () => {
  assert.equal(foldToRise(0), 0);
  assert.equal(foldToRise(1), 1);
  assert.equal(foldToRise(-1), 0);
  let peak = 0;
  for (let f = 0; f <= 1; f += 0.01) peak = Math.max(peak, foldToRise(f));
  assert.ok(peak > 1.01 && peak < 1.04, `peak ${peak}`);
});

test("hymn_books: one riser under steps_top, clear of Dad at steps_foot", () => {
  const risers = risersFor(set);
  assert.equal(risers.length, 1);
  const r = risers[0];
  assert.equal(r.key, "steps_top");
  assert.ok(Math.abs(r.x - 0.2) < 1e-9 && Math.abs(r.h - 0.3) < 1e-9);
  // steps_foot is at x = 0: the riser must not reach under Dad there.
  assert.ok(r.x - r.halfWidth >= 0.07 - 1e-9);
  // The books land beside Mom at x = 0.12: still on the riser.
  assert.ok(r.x - r.halfWidth <= 0.12);
  // It follows the middle layer (z 0.35), the one behind the acting plane.
  assert.equal(r.layer, 1);
});

test("risen: every mount is the identity (the timeline's positions as compiled)", () => {
  const s = new Standing(memory.sets!, frameAt(1));
  for (const [x, y, z] of [
    [0, 0, 0.5],
    [0.2, 0.3, 0.5],
    [-0.6, 0, 0.5],
  ]) {
    const out = s.mount(x, y, z, m());
    assert.deepEqual([out.angle, out.dy, out.dz], [0, 0, 0]);
  }
});

test("the reveal caps every fold: a page turned over the risen scene lays it flat", () => {
  let reveal = 0;
  const s = new Standing(memory.sets!, frameAt(1), () => reveal);
  const f = frameAt(1);
  assert.equal(s.fold(f.layers[0]), 0);
  assert.ok(Math.abs(s.mount(0, 0, 0.5, m()).angle - Math.PI / 2) < 1e-9);
  reveal = 0.4;
  assert.equal(s.fold(f.layers[0]), 0.4);
  reveal = 1;
  assert.equal(s.fold(f.layers[0]), 1);
  assert.equal(s.mount(0, 0, 0.5, m()).angle, 0);
});

test("folded: figures lie flat towards the reader, and riders sink with their riser", () => {
  const s = new Standing(memory.sets!, frameAt(0));
  const ground = s.mount(0, 0, 0.5, m());
  assert.ok(Math.abs(ground.angle - Math.PI / 2) < 1e-9);
  assert.ok(ground.dy > 0 && ground.dz === 0);
  const top = s.mount(0.2, 0.3, 0.5, m());
  assert.ok(Math.abs(top.angle - Math.PI / 2) < 1e-9);
  // The riser's top drops to the page (less a card's lift) and moves forward by its wall height.
  const H = 0.3 - RISER_THICK;
  assert.ok(Math.abs(top.dz - H) < 1e-9);
  assert.ok(0.3 + top.dy > 0 && 0.3 + top.dy < 0.08);
});
