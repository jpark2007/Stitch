"use client";

/**
 * The hand-built test diorama for /render/demo: a village green at evening.
 * A sky card with layered bands, a V-folded church on a stone riser (a
 * parallel-fold floating layer), a cottage with a chimney, a pair of trees
 * and two flowering bushes rise from the page at six depths (back first),
 * over a lawn and a path glued flat to the page. Then a slow wide -> dolly-in
 * camera move and a confetti throw; ?effect swaps in chimney smoke, steam
 * or rain.
 *
 * Layers follow the shape of a saved set (ARCHITECTURE.md section 5: art,
 * depth, height), kept local until the shared contracts land.
 */

import { useMemo } from "react";
import { cameraCues, stagePoses, type CameraCue, type CameraPath } from "@/components/stage/CameraRig";
import { getCutout, type CutoutSpec } from "@/components/stage/cutout";
import { PaperEffect, type EffectKind, type Emitter, type PaperEffectProps } from "@/components/stage/Effects";
import { FloatingLayer, PagePatch, SetPiece, usePieceEmitter, type PiecePlacement } from "@/components/stage/SetPiece";
import {
  COTTAGE_CHIMNEY,
  PALETTE,
  PATH_ASPECT,
  bushLeft,
  bushRight,
  church,
  cottage,
  lawn,
  path,
  skyCard,
  steps,
  trees,
} from "@/components/stage/art";
import { linear, type Vec3 } from "@/components/stage/timeline";

export const DEMO_OPEN_AT = 0.5;

/** Six rise slots, back to front. */
const COUNT = 6;

interface DemoLayer extends PiecePlacement {
  art: CutoutSpec;
}

const LAWN = { width: 1.94, depth: 2.9, x: 0.13, z: -0.05 };
const lawnArt = lawn(LAWN.width / LAWN.depth);
/** The path runs from the riser's front edge towards the reader. */
const PATH_W = 0.5;
const PATH = { width: PATH_W, x: -0.04, z: -0.5 + PATH_W / PATH_ASPECT / 2 };

/** The church's stone riser: back fold, size, and where the church stands on it. */
const RISER = { x: -0.04, z: -0.8, width: 1.02, height: 0.085, depth: 0.3 };
const RISER_TOP: Vec3 = [RISER.x, RISER.height + 0.007, RISER.z];
const CHURCH: DemoLayer = { art: church, height: 1.12, x: 0, z: 0.19, order: 2, count: COUNT, vfold: 0.38 };

const LAYERS: DemoLayer[] = [
  { art: skyCard, height: 1.62, x: 0.08, z: -1.25, order: 0, count: COUNT },
  { art: cottage, height: 0.6, x: 0.72, z: -0.42, yaw: -0.28, order: 3, count: COUNT },
  { art: trees, height: 0.84, x: -0.58, z: -0.1, order: 4, count: COUNT },
  { art: bushLeft, height: 0.33, x: -0.6, z: 0.62, yaw: 0.06, order: 5, count: COUNT },
  { art: bushRight, height: 0.34, x: 0.52, z: 0.7, yaw: -0.08, order: 5, count: COUNT },
];

const SKY = LAYERS[0];
const COTTAGE = LAYERS[1];
const CHURCH_ON_RISER: PiecePlacement = { ...CHURCH, x: RISER.x + (CHURCH.x ?? 0), z: RISER.z + (CHURCH.z ?? 0), base: [0, RISER_TOP[1], 0] };

export const DEMO_CUTOUTS: CutoutSpec[] = [skyCard, church, cottage, trees, bushLeft, bushRight, steps, lawnArt, path];

const poses = stagePoses();

/** Wide while the set rises, then a slow dolly in towards the church door,
 *  written as screenplay camera cues. */
export const DEMO_CUES: CameraCue[] = [
  { t: 0, move: "wide", dur: 0 },
  { t: 2.6, move: "dolly_to", target: [0.0, 0.48, -0.55], distance: 3.2, height: 0.86, dur: 6 },
  // Never quite stop: a slow drift keeps the held shot alive.
  { t: 8.6, move: "dolly_to", target: [-0.04, 0.48, -0.55], distance: 3.0, height: 0.8, dur: 11.4, ease: linear },
];

export const demoCamera: CameraPath = cameraCues(DEMO_CUES, poses.wide);

/** Confetti thrown over the church door once the set has risen. */
const CONFETTI_AT = 4.6;

export function DemoDiorama({ effect }: { effect?: EffectKind | null }) {
  const cutouts = useMemo(() => LAYERS.map((l) => getCutout(l.art)), []);
  const churchCut = useMemo(() => getCutout(CHURCH.art), []);
  const stepsCut = useMemo(() => getCutout(steps), []);
  const lawnCut = useMemo(() => getCutout(lawnArt), []);
  const pathCut = useMemo(() => getCutout(path), []);

  // Emitters: smoke and steam from the chimney top, confetti over the church
  // door, rain over the page; each only while its piece stands.
  const chimney = usePieceEmitter(COTTAGE, COTTAGE_CHIMNEY[0], COTTAGE_CHIMNEY[1]);
  const door = usePieceEmitter(CHURCH_ON_RISER, 0, 0.3);
  const sky = usePieceEmitter(SKY, 0, 0.5);
  const effects = useMemo<Record<EffectKind, Omit<PaperEffectProps, "kind">>>(() => {
    const fixed = (gate: Emitter, at: Vec3): Emitter => (t, out) => {
      const on = gate(t, out);
      out[0] = at[0];
      out[1] = at[1];
      out[2] = at[2];
      return on;
    };
    return {
      confetti: { emitter: fixed(door, [-0.02, 0.4, -0.25]), spread: [0.55, 0.4], count: 90, start: CONFETTI_AT },
      steam: { emitter: chimney, scale: 0.8, wind: -0.1 },
      rain: { emitter: fixed(sky, [0.1, 0, -0.1]), spread: [0.9, 1.2] },
      smoke: { emitter: chimney, scale: 0.62, wind: -0.32 },
    };
  }, [chimney, door, sky]);

  const kind = effect === undefined ? "confetti" : effect;
  return (
    <>
      <PagePatch cutout={lawnCut} width={LAWN.width} x={LAWN.x} z={LAWN.z} />
      <PagePatch cutout={pathCut} width={PATH.width} x={PATH.x} z={PATH.z} layer={1} />
      {LAYERS.map((l, i) => (
        <SetPiece
          key={l.art.key}
          cutout={cutouts[i]}
          height={l.height}
          x={l.x}
          z={l.z}
          yaw={l.yaw}
          vfold={l.vfold}
          order={l.order}
          count={l.count}
          backColor={PALETTE.cardBack}
        />
      ))}
      <FloatingLayer
        x={RISER.x}
        z={RISER.z}
        width={RISER.width}
        height={RISER.height}
        depth={RISER.depth}
        order={1}
        count={COUNT}
        front={stepsCut}
        color={PALETTE.stone}
      >
        <SetPiece
          cutout={churchCut}
          height={CHURCH.height}
          x={CHURCH.x}
          z={CHURCH.z}
          vfold={CHURCH.vfold}
          order={CHURCH.order}
          count={COUNT}
          backColor={PALETTE.cardBack}
        />
      </FloatingLayer>
      {kind && <PaperEffect kind={kind} {...effects[kind]} />}
    </>
  );
}
