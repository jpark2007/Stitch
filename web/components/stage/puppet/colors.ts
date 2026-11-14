/**
 * A person's paper colours, from their character sheet colour and the item:
 * the near side of the body, the far side (in shade), the lighter head and
 * hands, the pale cut edge, and the item's own papers. One set of materials
 * per puppet, built on the shared paper textures (web/components/paper).
 */

import { useEffect, useMemo } from "react";
import { Color, type MeshStandardMaterial } from "three";
import { createCardEdgeMaterial, createPaperMaterial } from "@/components/paper/materials";
import type { ItemKind } from "./items";
import type { ItemTone } from "./itemArt";

export type BodyTone = "body" | "far" | "light" | "farLight";

export interface PuppetMaterials {
  body: Record<BodyTone, MeshStandardMaterial>;
  /** Cut edge of the body pieces: the card's pale core. */
  edge: MeshStandardMaterial;
  item: Record<ItemTone, MeshStandardMaterial>;
  itemEdge: MeshStandardMaterial;
}

const CREAM = new Color("#f4ead6");
const INK = "#3b302a";

/** Default item paper per kind when the text names no colour. */
const ITEM_DEFAULT: Record<ItemKind, string> = {
  flat_cap: "#a88a63",
  hat: "#c4a37a",
  beanie: "#b9573d",
  scarf: "#d3a03d",
  glasses: INK,
  bow: "#b9573d",
  headband: "#d3a03d",
  token: "#e8c25a",
};

/** Perceptual-ish distance between two colours (sRGB, 0..~1.7). */
function distance(a: Color, b: Color): number {
  const ah = a.getHSL({ h: 0, s: 0, l: 0 });
  const bh = b.getHSL({ h: 0, s: 0, l: 0 });
  const dh = Math.min(Math.abs(ah.h - bh.h), 1 - Math.abs(ah.h - bh.h)) * 2;
  return dh * Math.min(ah.s, bh.s) + Math.abs(ah.l - bh.l) * 1.5 + Math.abs(ah.s - bh.s) * 0.5;
}

export interface PuppetPalette {
  body: Record<BodyTone, Color>;
  edge: Color;
  item: Record<ItemTone, Color>;
}

/** Shift a colour's lightness and saturation in HSL, keeping its hue. */
function shade(c: Color, dl: number, sMul = 1): Color {
  const hsl = c.getHSL({ h: 0, s: 0, l: 0 });
  return new Color().setHSL(hsl.h, Math.min(1, hsl.s * sMul), Math.min(0.92, Math.max(0.05, hsl.l + dl)));
}

/** The colours (no GPU work), for previews and tests. */
export function puppetPalette(color: string, kind: ItemKind, itemColor: string | null): PuppetPalette {
  const base = new Color(color);
  const body = base.clone();
  const far = base.clone().multiplyScalar(0.8);
  // Head and hands: the same paper a shade lighter (hue kept, so a blue
  // puppet never gets a grey head).
  const light = shade(base, 0.09, 0.92);
  const farLight = light.clone().multiplyScalar(0.84);
  const edge = base.clone().lerp(CREAM, 0.72);

  let main = new Color(itemColor ?? ITEM_DEFAULT[kind]);
  if (distance(main, base) < 0.18) {
    // An item the same colour as its wearer disappears. A named colour keeps
    // its hue and moves darker (or lighter on dark puppets); an unnamed
    // default is swapped for cream.
    if (!itemColor) main = new Color("#efe4cc");
    else main = shade(main, base.getHSL({ h: 0, s: 0, l: 0 }).l > 0.32 ? -0.14 : 0.16);
  }
  const dark = main.clone().multiplyScalar(0.74);
  const lightItem = main.clone().lerp(CREAM, 0.5);
  const ink = kind === "glasses" && itemColor ? main.clone() : new Color(INK);
  return { body: { body, far, light, farLight }, edge, item: { main, dark, light: lightItem, ink } };
}

function createPuppetMaterials(color: string, kind: ItemKind, itemColor: string | null): PuppetMaterials {
  const p = puppetPalette(color, kind, itemColor);
  // Smooth coloured paper (the card grain reads as felt at puppet scale).
  const card = (c: Color) => createPaperMaterial({ color: c, grain: 0.55, roughness: 0.9 });
  return {
    body: { body: card(p.body.body), far: card(p.body.far), light: card(p.body.light), farLight: card(p.body.farLight) },
    edge: createCardEdgeMaterial({ color: p.edge, darken: 0.04 }),
    item: { main: card(p.item.main), dark: card(p.item.dark), light: card(p.item.light), ink: card(p.item.ink) },
    itemEdge: createCardEdgeMaterial({ color: p.item.main.clone().lerp(CREAM, 0.6), darken: 0.04 }),
  };
}

function disposeAll(m: PuppetMaterials) {
  const all = new Set<MeshStandardMaterial>([...Object.values(m.body), m.edge, ...Object.values(m.item), m.itemEdge]);
  all.forEach((x) => x.dispose());
}

/** Materials for one puppet; rebuilt when the colours change, disposed on unmount. */
export function usePuppetMaterials(color: string, kind: ItemKind, itemColor: string | null): PuppetMaterials {
  const mats = useMemo(() => createPuppetMaterials(color, kind, itemColor), [color, kind, itemColor]);
  useEffect(() => () => disposeAll(mats), [mats]);
  return mats;
}
