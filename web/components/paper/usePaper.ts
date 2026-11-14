"use client";

/**
 * React hooks over ./materials. Use them inside an r3f <Canvas> (or any client
 * component): each returns a memoised material that is rebuilt only when its
 * options change and disposed on unmount. Shared textures are never disposed.
 *
 *   const paper = usePaperMaterial({ color: "#f3ead8" });
 *   return <mesh material={paper}>...</mesh>;
 */

import { useEffect, useMemo } from "react";
import type { Material } from "three";
import {
  createCardBoxMaterials,
  createCardMaterial,
  createCutoutMaterial,
  createPaperMaterial,
  createWoodMaterial,
  type CutoutMaterialOptions,
  type DeckledMaskOptions,
  type PaperMaterialOptions,
} from "./materials";

/** Dispose a material (or array of them) when it is replaced or unmounted. */
function useDisposeOnChange<T extends Material | Material[]>(value: T): T {
  useEffect(
    () => () => {
      const list: Material[] = Array.isArray(value) ? value : [value];
      new Set(list).forEach((m) => m.dispose());
    },
    [value],
  );
  return value;
}

/*
 * Hook options are plain data only: a colour as a CSS string or hex number,
 * and mask *options* (never a prebuilt DeckledMask or a THREE.Color). The memo
 * key is built from explicit primitive fields, so inline option objects do not
 * rebuild the material on every render, and nothing is ever serialised.
 */

/** A colour as plain data: "#c46a49", "tomato", or 0xc46a49. */
export type PaperColor = string | number;

export type PaperHookOptions = Omit<PaperMaterialOptions, "color"> & { color?: PaperColor };
export type CardHookOptions = Omit<PaperHookOptions, "kind">;
export type CardBoxHookOptions = CardHookOptions & { edgeColor?: PaperColor };
export type CutoutHookOptions = Omit<CutoutMaterialOptions, "color" | "mask"> & {
  color?: PaperColor;
  mask?: DeckledMaskOptions;
};
export type WoodHookOptions = { repeat?: number | [number, number]; color?: PaperColor };

const part = (v: unknown) => (v === undefined ? "" : String(v));
const repeatKey = (r: PaperMaterialOptions["repeat"]) => (Array.isArray(r) ? `${r[0]}x${r[1]}` : part(r));

function paperKey(o: PaperHookOptions = {}) {
  return [part(o.color), part(o.roughness), part(o.grain), part(o.kind), repeatKey(o.repeat), part(o.side)].join("|");
}

function maskKey(m?: DeckledMaskOptions) {
  if (!m) return "";
  return [part(m.aspect), part(m.style), part(m.amount), part(m.seed), part(m.resolution), part(m.cornerRadius)].join(",");
}

/* The memo deps are primitive keys; the factories close over the latest
   options, which by construction produce the same material for the same key. */

export function usePaperMaterial(options?: PaperHookOptions) {
  const k = paperKey(options);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useDisposeOnChange(useMemo(() => createPaperMaterial(options), [k]));
}

export function useCardMaterial(options?: CardHookOptions) {
  const k = paperKey(options);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useDisposeOnChange(useMemo(() => createCardMaterial(options), [k]));
}

export function useCardBoxMaterials(options?: CardBoxHookOptions) {
  const k = `${paperKey(options)}|${part(options?.edgeColor)}`;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useDisposeOnChange(useMemo(() => createCardBoxMaterials(options), [k]));
}

export function useCutoutMaterial(options?: CutoutHookOptions) {
  const k = `${paperKey(options)}|${part(options?.rim)}|${maskKey(options?.mask)}`;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useDisposeOnChange(useMemo(() => createCutoutMaterial(options), [k]));
}

export function useWoodMaterial(options?: WoodHookOptions) {
  const k = `${part(options?.color)}|${repeatKey(options?.repeat)}`;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useDisposeOnChange(useMemo(() => createWoodMaterial(options), [k]));
}
