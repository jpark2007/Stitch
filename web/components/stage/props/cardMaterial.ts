/**
 * Materials for a prop card: the art as an alpha-cut sheet of paper.
 *
 * The art supplies colour (and alpha, for PNG cutouts); the shared paper
 * textures from components/paper supply the grain relief and roughness, so a
 * prop catches the light like the scenery does. Alpha-to-coverage gives soft
 * cut edges without transparency sorting, and alphaTest makes the shadow
 * follow the silhouette.
 */

import { Color, DoubleSide, MeshStandardMaterial, Vector2, type Texture } from "three";
import { getPaperTextures } from "@/components/paper/materials";

export interface CardMaterialOptions {
  /** Art colour map (alpha in its alpha channel for loaded cutouts). */
  map: Texture;
  /** Separate silhouette mask (placeholders), white inside. */
  alphaMap?: Texture | null;
  /** Card size in stage units, to keep the grain at a paper-like scale. */
  width: number;
  height: number;
  /** Multiplies the art: white for the face, dark for the card's edge. */
  tint?: string;
  /** Grain relief, 0..2. Default 0.7. */
  grain?: number;
}

/** Grain tiles per stage unit (about the set's scale), quantised so cards of
 *  similar size share one cached texture set. */
function grainRepeat(size: number): number {
  const r = Math.max(0.5, size * 2.2);
  return Math.round(r * 4) / 4;
}

export function createCardMaterial(o: CardMaterialOptions): MeshStandardMaterial {
  const grain = getPaperTextures("paper", [grainRepeat(o.width), grainRepeat(o.height)]);
  const m = new MeshStandardMaterial({
    color: new Color(o.tint ?? "#ffffff"),
    roughness: 0.9,
    metalness: 0,
    side: DoubleSide,
  });
  m.map = o.map;
  m.alphaMap = o.alphaMap ?? null;
  m.normalMap = grain.normalMap;
  const g = o.grain ?? 0.7;
  m.normalScale = new Vector2(g, g);
  m.roughnessMap = grain.roughnessMap;
  m.alphaTest = 0.5;
  m.alphaToCoverage = true;
  m.name = "prop:card";
  return m;
}
