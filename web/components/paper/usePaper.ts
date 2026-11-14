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

// Options are keyed by value (JSON) so inline option objects don't rebuild the
// material on every render; the memo re-reads them from the key.
const keyOf = (o: unknown) => JSON.stringify(o ?? {});
const fromKey = <T,>(k: string) => JSON.parse(k) as T;

export function usePaperMaterial(options?: PaperMaterialOptions) {
  const k = keyOf(options);
  return useDisposeOnChange(useMemo(() => createPaperMaterial(fromKey(k)), [k]));
}

export function useCardMaterial(options?: Omit<PaperMaterialOptions, "kind">) {
  const k = keyOf(options);
  return useDisposeOnChange(useMemo(() => createCardMaterial(fromKey(k)), [k]));
}

export function useCardBoxMaterials(options?: Parameters<typeof createCardBoxMaterials>[0]) {
  const k = keyOf(options);
  return useDisposeOnChange(useMemo(() => createCardBoxMaterials(fromKey(k)), [k]));
}

/** Pass mask *options* (not a prebuilt mask) so the key stays serialisable. */
export function useCutoutMaterial(options?: CutoutMaterialOptions) {
  const k = keyOf(options);
  return useDisposeOnChange(useMemo(() => createCutoutMaterial(fromKey(k)), [k]));
}

export function useWoodMaterial(options?: Parameters<typeof createWoodMaterial>[0]) {
  const k = keyOf(options);
  return useDisposeOnChange(useMemo(() => createWoodMaterial(fromKey(k)), [k]));
}
