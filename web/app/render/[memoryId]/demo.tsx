"use client";

/**
 * The hand-built test diorama for /render/demo: a sky card, a V-folded church,
 * a pair of trees and a hedge at four depths, rising on load (back first),
 * then a slow wide -> dolly-in camera move and a confetti throw.
 *
 * Layers follow the shape of a saved set (ARCHITECTURE.md section 5: art,
 * depth, height), kept local until the shared contracts land.
 */

import { useMemo } from "react";
import { cameraCues, stagePoses, type CameraCue, type CameraPath } from "@/components/stage/CameraRig";
import { getCutout, type CutoutSpec } from "@/components/stage/cutout";
import { PaperEffect, type EffectKind, type PaperEffectProps } from "@/components/stage/Effects";
import { SetPiece } from "@/components/stage/SetPiece";
import { PALETTE, bush, church, skyCard, trees } from "@/components/stage/art";
import { linear } from "@/components/stage/timeline";

export const DEMO_OPEN_AT = 0.5;

interface DemoLayer {
  art: CutoutSpec;
  /** 0 is the back layer. */
  depth: number;
  height: number;
  x: number;
  z: number;
  vfold?: number;
}

const LAYERS: DemoLayer[] = [
  { art: skyCard, depth: 0, height: 1.62, x: 0, z: -1.15 },
  { art: church, depth: 1, height: 1.16, x: 0.3, z: -0.5, vfold: 0.38 },
  { art: trees, depth: 2, height: 0.92, x: -0.6, z: 0.02 },
  { art: bush, depth: 3, height: 0.34, x: 0.05, z: 0.6 },
];

const poses = stagePoses();

/** Wide while the set rises, then a slow dolly in towards the church door,
 *  written as screenplay camera cues. */
export const DEMO_CUES: CameraCue[] = [
  { t: 0, move: "wide", dur: 0 },
  { t: 2.6, move: "dolly_to", target: [0.2, 0.5, -0.5], distance: 3.05, height: 0.8, dur: 6 },
  // Never quite stop: a slow drift keeps the held shot alive.
  { t: 8.6, move: "dolly_to", target: [0.16, 0.5, -0.5], distance: 2.85, height: 0.74, dur: 11.4, ease: linear },
];

export const demoCamera: CameraPath = cameraCues(DEMO_CUES, poses.wide);

const EFFECTS: Record<EffectKind, Omit<PaperEffectProps, "kind">> = {
  confetti: { origin: [0.25, 0.4, -0.05], spread: [0.55, 0.38], count: 80, start: 4.6 },
  steam: { origin: [-0.08, 0.04, 0.32], start: 0 },
  rain: { origin: [0, 0, -0.1], start: 0 },
  smoke: { origin: [0.62, 0.46, -0.62], start: 0 },
};

export function DemoDiorama({ effect }: { effect?: EffectKind | null }) {
  const cutouts = useMemo(() => LAYERS.map((l) => getCutout(l.art)), []);
  const kind = effect === undefined ? "confetti" : effect;
  // An explicitly requested effect runs from the start, so any ?t shows it.
  const fx = kind ? { ...EFFECTS[kind], start: effect ? 0 : EFFECTS[kind].start } : null;
  return (
    <>
      {LAYERS.map((l, i) => (
        <SetPiece
          key={l.art.key}
          cutout={cutouts[i]}
          height={l.height}
          x={l.x}
          z={l.z}
          vfold={l.vfold}
          order={l.depth}
          count={LAYERS.length}
          backColor={PALETTE.cardBack}
        />
      ))}
      {kind && fx && <PaperEffect kind={kind} {...fx} />}
    </>
  );
}
