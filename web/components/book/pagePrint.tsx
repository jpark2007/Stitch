"use client";

/**
 * Things laid on the stage page that must follow its paper: an open page is
 * not flat, it swells a little across its width (leafShape.pageProfile), so
 * a flat decal would sink into it at one end and float at the other.
 *
 *   <PagePrint map={texture} width depth x z opacity />
 *     a printed plane in the stage slot, bent to the page's swell
 *   usePageSurface()
 *     the page's height at a slot x, for something resting on it
 *
 * Both read the book's runtime from context (Book3D provides it around every
 * stage slot) and re-bend only when the page's rest shape changes.
 */

import { useFrame } from "@react-three/fiber";
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { PlaneGeometry, type BufferGeometry, type Mesh, type MeshStandardMaterial, type Texture } from "three";
import { PAGE_W, pageProfile } from "./leafShape";
import type { BookRuntime } from "./runtime";
import { STAGE_SLOT } from "./StoryPage";

const BookRuntimeContext = createContext<BookRuntime | null>(null);

export function BookRuntimeProvider({ runtime, children }: { runtime: BookRuntime; children: ReactNode }) {
  return <BookRuntimeContext.Provider value={runtime}>{children}</BookRuntimeContext.Provider>;
}

/** How high above the paper prints sit (clear of the page, under anything standing on it). */
export const PRINT_LIFT = 0.0007;

/**
 * The right page's surface height at slot-space x, relative to the slot's
 * origin (which the book keeps on the surface at the page's centre line).
 */
export function pageHeightAt(rt: BookRuntime | null, x: number): number {
  if (!rt) return 0;
  const bx = Math.min(PAGE_W, Math.max(0, STAGE_SLOT.x + x));
  return pageProfile(bx, rt.depthR, rt.bow) - pageProfile(PAGE_W / 2, rt.depthR, rt.bow);
}

/** The book's runtime, for pageHeightAt (null outside a book). */
export function usePageSurface(): BookRuntime | null {
  return useContext(BookRuntimeContext);
}

export interface PagePrintProps {
  map: Texture;
  /** Size in world units: x across the page, z down it. */
  width: number;
  depth: number;
  /** Centre, slot space. */
  x?: number;
  z?: number;
  /** Live opacity (read every frame), e.g. a fade or the stage's reveal. */
  opacity?: { readonly current: number };
  renderOrder?: number;
}

const SEGMENTS = 24;

/** Lays a flat plane's vertices (centred at slot x `x0`) on the page's surface. */
export function bendToPage(geometry: BufferGeometry, rt: BookRuntime | null, x0: number, lift = PRINT_LIFT) {
  const pos = geometry.attributes.position;
  for (let i = 0; i < pos.count; i++) pos.setY(i, pageHeightAt(rt, x0 + pos.getX(i)) + lift);
  pos.needsUpdate = true;
  geometry.computeBoundingSphere();
}

/**
 * A printed plane on the stage page: ink in `map`'s alpha, lit like the
 * paper it is printed on, bent along x to the page's swell.
 */
export function PagePrint({ map, width, depth, x = 0, z = 0, opacity, renderOrder = 1 }: PagePrintProps) {
  const rt = usePageSurface();
  const geometry = useMemo(() => {
    const g = new PlaneGeometry(width, depth, SEGMENTS, 1);
    g.rotateX(-Math.PI / 2);
    return g;
  }, [width, depth]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  const mesh = useRef<Mesh>(null);
  const mat = useRef<MeshStandardMaterial>(null);
  const bent = useRef<{ version: number; geometry: BufferGeometry | null; x: number }>({ version: -1, geometry: null, x: 0 });

  useFrame(() => {
    const m = mesh.current;
    if (!m) return;
    const o = opacity ? opacity.current : 1;
    if (mat.current && mat.current.opacity !== o) mat.current.opacity = o;
    m.visible = o > 0.003;
    // Re-bend only when the page's rest shape changes (a leaf landing).
    const version = rt ? rt.restVersion : 0;
    if (version === bent.current.version && m.geometry === bent.current.geometry && x === bent.current.x) return;
    bent.current = { version, geometry: m.geometry, x };
    bendToPage(m.geometry, rt, x);
  });

  return (
    <mesh ref={mesh} geometry={geometry} position={[x, 0, z]} renderOrder={renderOrder} receiveShadow>
      <meshStandardMaterial
        ref={mat}
        map={map}
        transparent
        depthWrite={false}
        roughness={0.95}
        metalness={0}
        polygonOffset
        polygonOffsetFactor={-1}
        polygonOffsetUnits={-2}
      />
    </mesh>
  );
}
