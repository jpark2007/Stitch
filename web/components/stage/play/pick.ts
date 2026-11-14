/**
 * Picking a cutout on the stage by its real shape, for click-to-explain.
 *
 *   pickCutout(object, raycaster, wings, scratch)       nearest hit on its paper, or null
 *   pickNear(object, raycaster, camera, x, y, size, r)  the same, forgiving by r px
 *   cutoutBox(object, out)                               its paper's world box
 *
 * A puppet is cut card (its outline is the geometry), a prop is a card
 * whose art is cut by an alpha map: a hit counts only where the paper is,
 * not on its bounding card, and not where the wings clip it. Soft shadows
 * (transparent halo planes) never count. A ring of rays `r` px out gives a
 * finger a little slack round the edge.
 *
 * Allocation-free after the first pick of each alpha map (whose alpha is
 * read back once, small, and cached by image).
 */

import {
  Box3,
  Vector2,
  Vector3,
  type Camera,
  type Intersection,
  type Material,
  type Mesh,
  type Object3D,
  type Plane,
  type Raycaster,
  type Texture,
} from "three";

/** Alpha below this is outside the cut (the cards' alphaTest). */
const CUT = 0.5;
/** Alpha maps are read back at most this size. */
const READ_PX = 160;

interface AlphaGrid {
  w: number;
  h: number;
  /** One byte per texel, 0..255. */
  a: Uint8Array;
}

const grids = new WeakMap<object, AlphaGrid | null>();

/** The alpha of a texture's image, read back once (null when it cannot be read). */
function gridOf(tex: Texture, channel: "g" | "a"): AlphaGrid | null {
  const img = tex.image as (CanvasImageSource & { width: number; height: number }) | null | undefined;
  if (!img || !img.width || !img.height) return null;
  const hit = grids.get(img);
  if (hit !== undefined) return hit;
  let grid: AlphaGrid | null = null;
  try {
    const k = Math.min(1, READ_PX / Math.max(img.width, img.height));
    const w = Math.max(1, Math.round(img.width * k));
    const h = Math.max(1, Math.round(img.height * k));
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (ctx) {
      ctx.drawImage(img, 0, 0, w, h);
      const px = ctx.getImageData(0, 0, w, h).data;
      const a = new Uint8Array(w * h);
      const o = channel === "g" ? 1 : 3;
      for (let i = 0; i < a.length; i++) a[i] = px[i * 4 + o];
      grid = { w, h, a };
    }
  } catch {
    // A tainted or unready image: treat its whole card as paper.
    grid = null;
  }
  // An image not yet loaded is not cached, so it is read once it is.
  if (grid || (img as HTMLImageElement).complete !== false) grids.set(img, grid);
  return grid;
}

const uv = new Vector2();

/** The paper is there at this hit (alpha-cut cards), or the material has no cut. */
function isPaper(hit: Intersection, mat: Material): boolean {
  const m = mat as Material & { alphaMap?: Texture | null; map?: Texture | null };
  if (!(m.alphaTest > 0) || !hit.uv) return true;
  const tex = m.alphaMap ?? m.map ?? null;
  if (!tex) return true;
  const grid = gridOf(tex, m.alphaMap ? "g" : "a");
  if (!grid) return true;
  tex.updateMatrix();
  uv.copy(hit.uv);
  tex.transformUv(uv);
  const x = Math.min(grid.w - 1, Math.max(0, Math.floor(uv.x * grid.w)));
  const y = Math.min(grid.h - 1, Math.max(0, Math.floor(uv.y * grid.h)));
  return grid.a[y * grid.w + x] >= CUT * 255;
}

function materialOf(mesh: Mesh, hit: Intersection): Material | null {
  const m = mesh.material as Material | Material[];
  if (!Array.isArray(m)) return m;
  return m[hit.face?.materialIndex ?? 0] ?? m[0] ?? null;
}

/** A soft shadow or other see-through layer, never part of the cutout. */
function isShade(mesh: Mesh): boolean {
  const m = mesh.material as Material | Material[];
  const first = Array.isArray(m) ? m[0] : m;
  return !first || first.transparent || !first.visible;
}

function clipped(p: Vector3, wings: readonly Plane[]): boolean {
  for (let i = 0; i < wings.length; i++) if (wings[i].distanceToPoint(p) < 0) return true;
  return false;
}

/** Shown, with every ancestor shown too (traverseVisible stops at hidden parents). */
function eachVisibleMesh(object: Object3D, fn: (m: Mesh) => void) {
  object.traverseVisible((o) => {
    const mesh = o as Mesh;
    if (mesh.isMesh && !isShade(mesh)) fn(mesh);
  });
}

/**
 * The nearest hit of `raycaster` on the paper of `object` (a puppet's root
 * or a prop's group), or null. `scratch` collects raw hits; it is cleared.
 */
export function pickCutout(object: Object3D, raycaster: Raycaster, wings: readonly Plane[], scratch: Intersection[]): Intersection | null {
  let best: Intersection | null = null;
  for (let o: Object3D | null = object; o; o = o.parent) if (!o.visible) return null;
  eachVisibleMesh(object, (mesh) => {
    scratch.length = 0;
    mesh.raycast(raycaster, scratch);
    for (const hit of scratch) {
      if (best && hit.distance >= best.distance) continue;
      if (clipped(hit.point, wings)) continue;
      const mat = materialOf(mesh, hit);
      if (!mat || !isPaper(hit, mat)) continue;
      best = hit;
    }
  });
  scratch.length = 0;
  return best;
}

const ndc = new Vector2();
const RING = 8;

/**
 * Like pickCutout for a pointer at NDC (x, y) on a canvas `size` CSS px,
 * but forgiving: when the pointer misses the paper, rays in a ring `r` px
 * out are tried too (a finger's slack). `raycaster` is re-aimed (and left
 * aimed at the pointer).
 */
export function pickNear(
  object: Object3D,
  raycaster: Raycaster,
  camera: Camera,
  x: number,
  y: number,
  size: { width: number; height: number },
  r: number,
  wings: readonly Plane[],
  scratch: Intersection[],
): Intersection | null {
  raycaster.setFromCamera(ndc.set(x, y), camera);
  let hit = pickCutout(object, raycaster, wings, scratch);
  if (hit || r <= 0) return hit;
  for (let ring = 0; ring < 2; ring++) {
    const k = ring === 0 ? 0.55 : 1;
    for (let i = 0; i < RING && !hit; i++) {
      const a = (i / RING) * Math.PI * 2 + ring * 0.4;
      raycaster.setFromCamera(
        ndc.set(x + ((Math.cos(a) * r * k) / Math.max(1, size.width)) * 2, y + ((Math.sin(a) * r * k) / Math.max(1, size.height)) * 2),
        camera,
      );
      hit = pickCutout(object, raycaster, wings, scratch);
    }
    if (hit) break;
  }
  raycaster.setFromCamera(ndc.set(x, y), camera);
  return hit;
}

const box = new Box3();

/** The world box of `object`'s paper (shadows left out); empty when nothing shows. */
export function cutoutBox(object: Object3D, out: Box3): Box3 {
  out.makeEmpty();
  object.updateWorldMatrix(true, true);
  eachVisibleMesh(object, (mesh) => {
    const g = mesh.geometry;
    if (!g.boundingBox) g.computeBoundingBox();
    if (!g.boundingBox) return;
    box.copy(g.boundingBox).applyMatrix4(mesh.matrixWorld);
    out.union(box);
  });
  return out;
}
