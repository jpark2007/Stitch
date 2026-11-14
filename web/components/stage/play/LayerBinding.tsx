"use client";

/**
 * Set layers, driven by their `set:<setId>.layer<n>` fold tracks.
 *
 * <LayerBinding> binds one layer's frame slot once and hands its fold to the
 * pieces below it through a RiseProvider, so the existing <SetPiece> (and
 * <FloatingLayer>) hinge it every frame: 0 flat on the page, 1 risen. The
 * linear fold is eased into a hinge angle with a small pop past upright near
 * the top, so cards spring up like paper rather than swing like doors, and
 * any fold value is still exactly one pose (scrubbing and ?t are exact).
 *
 * Placement (CONTRACTS.md "Stage space"): every layer spans the full stage
 * width (2 units), centred, standing at z = depth × 0.35, `height` tall.
 * Layers rise back first (the compiler's stagger), and fold flat in that
 * order on top of each other.
 *
 * <MarkRisers> stands a paper platform under every mark with a standing
 * surface above the page (h > 0), hinged on the layer behind it, so a puppet
 * on `steps_top` stands on steps instead of air.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Group } from "three";
import type { SetDef, SetLayer } from "@/lib/types";
import { getCutout, type Cutout } from "../cutout";
import { PALETTE } from "../art";
import { FloatingLayer, RiseProvider, SetPiece, type PopUpValue } from "../SetPiece";
import type { SetLayerFrame } from "../playback";
import { imageLayerSpec, loadImage, placeholderLayerSpec, riserArt, tone } from "./layerArt";
import { useScene } from "./SceneProvider";

/** Stage z of a layer depth (0 back, 1 middle, 2 front). */
export const layerZ = (depth: number) => depth * 0.35;

/**
 * Fold (0..1, linear in time) to hinge rise: eased, with a soft ~2% pop past
 * upright near the top that settles exactly at 1.
 */
export function foldToRise(fold: number): number {
  const f = fold <= 0 ? 0 : fold >= 1 ? 1 : fold;
  const s = f * f * (3 - 2 * f);
  return s + 1.5 * s * s * (1 - s);
}

/** A RiseProvider value that plays one layer frame's fold. */
function useFoldRise(lf: SetLayerFrame | null): PopUpValue {
  return useMemo<PopUpValue>(() => ({ rise: () => (lf ? foldToRise(lf.fold) : 1) }), [lf]);
}

/** Shows its children while the layer stands, or while its set is the current shot's. */
function SetVisibility({ setId, folds, children }: { setId: string; folds: readonly SetLayerFrame[]; children: ReactNode }) {
  const { frame } = useScene();
  const group = useRef<Group>(null);
  useFrame(() => {
    const g = group.current;
    if (!g) return;
    let up = false;
    for (let i = 0; i < folds.length; i++) if (folds[i].fold > 0.001) up = true;
    // With no shots at all, every set with tracks shows.
    g.visible = up || !frame.shot || frame.shot.set === setId;
  });
  return <group ref={group}>{children}</group>;
}

/** The cutout for a layer: its image when it has one and it loads, else the placeholder card. */
function useLayerCutout(set: SetDef, index: number, layer: SetLayer): Cutout {
  const { resolveMedia } = useScene();
  const placeholder = useMemo(() => getCutout(placeholderLayerSpec(set.id, index, layer)), [set.id, index, layer]);
  const url = layer.art ? resolveMedia(layer.art) : null;
  const [art, setArt] = useState<{ url: string; cutout: Cutout } | null>(null);
  useEffect(() => {
    if (!url) return;
    let live = true;
    loadImage(url)
      .then((img) => {
        if (!live) return;
        const cutout = getCutout(imageLayerSpec(`set-art:${url}:${layer.height}`, img, layer.height, layer.color ?? undefined));
        setArt({ url, cutout });
      })
      .catch(() => {
        // Art that will not load keeps the placeholder card.
      });
    return () => {
      live = false;
    };
  }, [url, layer.height, layer.color]);
  return art && art.url === url ? art.cutout : placeholder;
}

export interface LayerBindingProps {
  set: SetDef;
  /** Index into set.layers */
  index: number;
  /** Rise slot: 0 rises first (the back). */
  order: number;
  count: number;
}

export function LayerBinding({ set, index, order, count }: LayerBindingProps) {
  const { frame } = useScene();
  const layer = set.layers[index];
  const lf = useMemo(() => frame.layersByTarget.get(`set:${set.id}.layer${index}`) ?? null, [frame, set.id, index]);
  const rise = useFoldRise(lf);
  const cutout = useLayerCutout(set, index, layer);
  return (
    <RiseProvider value={rise}>
      <SetPiece
        cutout={cutout}
        height={Math.max(0.05, layer.height)}
        z={layerZ(layer.depth)}
        order={order}
        count={count}
        backColor={PALETTE.cardBack}
      />
    </RiseProvider>
  );
}

/** Marks above the page: where something stands on a raised surface. */
interface Riser {
  key: string;
  x: number;
  z: number;
  h: number;
  halfWidth: number;
  /** Layer whose fold the riser follows (the one standing behind it). */
  layer: number;
  color: string;
}

const RISER_DEPTH = 0.24;
const THICK = 0.007;

function risersFor(set: SetDef): Riser[] {
  const marks = Object.entries(set.marks ?? {}).map(([name, m]) => ({
    name,
    x: 2 * (m[0] ?? 0.5) - 1,
    h: m[1] ?? 0,
    z: m[2] ?? 0.5,
  }));
  const out: Riser[] = [];
  for (const m of marks) {
    if (!(m.h > 0.02)) continue;
    // Keep clear of nearby marks on lower ground (someone stands there).
    let room = 0.16;
    for (const o of marks) if (o !== m && o.h < m.h - 0.02) room = Math.min(room, Math.abs(o.x - m.x) - 0.07);
    // The layer behind the mark, else the back one.
    let layer = 0;
    let best = -Infinity;
    set.layers.forEach((l, i) => {
      const z = layerZ(l.depth);
      if (z <= m.z && z > best) {
        best = z;
        layer = i;
      }
    });
    const behind = set.layers[layer];
    out.push({
      key: m.name,
      x: m.x,
      z: m.z,
      h: m.h,
      halfWidth: Math.max(0.08, room),
      layer,
      color: behind?.color ?? PALETTE.stone,
    });
  }
  return out;
}

function RiserBinding({ set, riser, order, count }: { set: SetDef; riser: Riser; order: number; count: number }) {
  const { frame } = useScene();
  const lf = useMemo(() => frame.layersByTarget.get(`set:${set.id}.layer${riser.layer}`) ?? null, [frame, set.id, riser.layer]);
  const rise = useFoldRise(lf);
  const width = riser.halfWidth * 2;
  const color = tone(riser.color, 0.04);
  const front = useMemo(() => getCutout(riserArt(`${set.id}:${riser.key}`, width / riser.h, color)), [set.id, riser.key, width, riser.h, color]);
  return (
    <RiseProvider value={rise}>
      <FloatingLayer
        x={riser.x}
        z={riser.z - RISER_DEPTH / 2}
        width={width}
        height={Math.max(0.01, riser.h - THICK)}
        depth={RISER_DEPTH}
        order={order}
        count={count}
        front={front}
        color={color}
        thickness={THICK}
      />
    </RiseProvider>
  );
}

/** Every layer (and riser) of every set the timeline folds. */
export function SetLayers() {
  const { frame, scene } = useScene();
  const sets = useMemo(() => {
    const byId = new Map(scene.sets.map((s) => [s.id, s]));
    const out: { set: SetDef; layers: number[]; folds: SetLayerFrame[]; risers: Riser[] }[] = [];
    for (const lf of frame.layers) {
      const set = byId.get(lf.setId);
      if (!set || !set.layers[lf.layer]) continue;
      let entry = out.find((e) => e.set.id === set.id);
      if (!entry) {
        entry = { set, layers: [], folds: [], risers: risersFor(set) };
        out.push(entry);
      }
      entry.layers.push(lf.layer);
      entry.folds.push(lf);
    }
    // Back layers rise first and fold on top of the front ones.
    for (const e of out) e.layers.sort((a, b) => e.set.layers[a].depth - e.set.layers[b].depth || a - b);
    return out;
  }, [frame, scene.sets]);

  return (
    <>
      {sets.map(({ set, layers, folds, risers }) => {
        const count = layers.length + risers.length;
        return (
          <SetVisibility key={set.id} setId={set.id} folds={folds}>
            {layers.map((i, order) => (
              <LayerBinding key={i} set={set} index={i} order={order} count={count} />
            ))}
            {risers.map((r, k) => (
              <RiserBinding key={r.key} set={set} riser={r} order={layers.length + k} count={count} />
            ))}
          </SetVisibility>
        );
      })}
    </>
  );
}

/** Every placeholder cutout a scene's sets need, to build ahead of mounting (preloadCutouts). */
export function sceneCutoutSpecs(sets: readonly SetDef[]) {
  return sets.flatMap((s) => s.layers.map((l, i) => placeholderLayerSpec(s.id, i, l)));
}
