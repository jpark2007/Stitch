/**
 * GPU resources shared by every puppet: one extruded card geometry and one
 * soft halo (contact shadow) per paper piece, the split-pin head, and the
 * materials that do not depend on a person's colour.
 *
 * Everything here is built lazily on first use (browser only) and cached for
 * the life of the page, the same way the paper textures are: puppets never
 * dispose these. `disposePuppetKit()` frees them on full teardown (tests).
 */

import {
  BufferAttribute,
  CanvasTexture,
  ClampToEdgeWrapping,
  ExtrudeGeometry,
  LinearFilter,
  MeshBasicMaterial,
  MeshStandardMaterial,
  NoColorSpace,
  PlaneGeometry,
  Shape,
  Path,
  SphereGeometry,
  type BufferGeometry,
} from "three";
import type { ItemKind } from "./items";
import { itemPieces, type ItemTone } from "./itemArt";
import { RIG } from "./rig";
import { bonePieces, bounds, deckle, ensureCCW, type Outline, type Piece } from "./shapes";

/** Card thickness, rig units (about 1.5 mm on a 20 cm doll). */
export const THICKNESS = 0.0065;
/** Gap between stacked pieces, rig units (pieces never touch). */
export const LAYER_GAP = 0.0105;
/** How many texture tiles per rig unit across a piece face. */
const UV_SCALE = 2.4;
/** Halo texture resolution, px per rig unit. */
const HALO_PX = 700;
/** Halo blur radius and spread, rig units. */
const HALO_BLUR = 0.0075;
const HALO_PAD = 0.024;

export interface PieceKit {
  geometry: ExtrudeGeometry;
  halo: PlaneGeometry;
  haloMaterial: MeshBasicMaterial;
}

export interface ItemPieceKit extends PieceKit {
  tone: ItemTone;
  layer: number;
}

interface Kit {
  bones: Record<string, PieceKit>;
  items: Map<ItemKind, ItemPieceKit[]>;
  pin: SphereGeometry;
  pinMaterial: MeshStandardMaterial;
}

let kit: Kit | null = null;

/* ------------------------------------------------------------------------ */

function toShape(o: Outline, holes: Outline[] | undefined): Shape {
  const s = new Shape();
  s.moveTo(o[0], o[1]);
  for (let i = 2; i < o.length; i += 2) s.lineTo(o[i], o[i + 1]);
  s.closePath();
  for (const h of holes ?? []) {
    // Holes run clockwise.
    const cw = ensureCCW(h).slice();
    const rev: number[] = [];
    for (let i = cw.length - 2; i >= 0; i -= 2) rev.push(cw[i], cw[i + 1]);
    const p = new Path();
    p.moveTo(rev[0], rev[1]);
    for (let i = 2; i < rev.length; i += 2) p.lineTo(rev[i], rev[i + 1]);
    p.closePath();
    s.holes.push(p);
  }
  return s;
}

/** Card piece: the outline extruded THICKNESS toward +z, UVs scaled to the grain. */
function cardGeometry(o: Outline, holes: Outline[] | undefined, seed: number): ExtrudeGeometry {
  const g = new ExtrudeGeometry(toShape(o, holes), {
    depth: THICKNESS,
    bevelEnabled: false,
    steps: 1,
    curveSegments: 1,
  });
  // Cap UVs are the shape's x/y in rig units: scale to the paper grain and
  // shift each piece to its own patch of the texture so no two match.
  const uv = g.getAttribute("uv") as BufferAttribute;
  const ox = ((seed * 0.6180339) % 1) * 7;
  const oy = ((seed * 0.4142135) % 1) * 7;
  for (let i = 0; i < uv.count; i++) {
    uv.setXY(i, uv.getX(i) * UV_SCALE + ox, uv.getY(i) * UV_SCALE + oy);
  }
  uv.needsUpdate = true;
  g.computeBoundingSphere();
  return g;
}

/** A soft, blurred silhouette of the piece on a plane just behind it. */
function haloFor(o: Outline, holes: Outline[] | undefined): { geometry: PlaneGeometry; material: MeshBasicMaterial } {
  const [x0, y0, x1, y1] = bounds(o);
  const w = x1 - x0 + HALO_PAD * 2;
  const h = y1 - y0 + HALO_PAD * 2;
  const W = Math.max(8, Math.ceil(w * HALO_PX));
  const H = Math.max(8, Math.ceil(h * HALO_PX));
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("puppet: 2D canvas unavailable");
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, W, H);
  ctx.filter = `blur(${(HALO_BLUR * HALO_PX).toFixed(2)}px)`;
  ctx.fillStyle = "#fff";
  const px = (x: number) => ((x - x0 + HALO_PAD) / w) * W;
  const py = (y: number) => H - ((y - y0 + HALO_PAD) / h) * H;
  ctx.beginPath();
  const trace = (p: Outline) => {
    ctx.moveTo(px(p[0]), py(p[1]));
    for (let i = 2; i < p.length; i += 2) ctx.lineTo(px(p[i]), py(p[i + 1]));
    ctx.closePath();
  };
  trace(o);
  for (const hole of holes ?? []) trace(hole);
  ctx.fill("evenodd");
  const tex = new CanvasTexture(canvas);
  tex.colorSpace = NoColorSpace;
  tex.wrapS = tex.wrapT = ClampToEdgeWrapping;
  tex.minFilter = LinearFilter;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;

  const geometry = new PlaneGeometry(w, h);
  geometry.translate((x0 + x1) / 2, (y0 + y1) / 2, 0);
  const material = new MeshBasicMaterial({
    color: "#3a2414",
    alphaMap: tex,
    transparent: true,
    opacity: 0.34,
    depthWrite: false,
    toneMapped: false,
  });
  material.name = "puppet:halo";
  return { geometry, material };
}

function buildPiece(p: Piece, seed: number, rough = 1): PieceKit {
  const outline = deckle(p.outline, { seed, wobble: 0.0022 * rough, tooth: 0.0011 * rough });
  const holes = p.holes?.map((h, i) => deckle(h, { seed: seed * 31 + i, wobble: 0.001 * rough, tooth: 0.0006 * rough }));
  const halo = haloFor(outline, holes);
  return { geometry: cardGeometry(outline, holes, seed), halo: halo.geometry, haloMaterial: halo.material };
}

function getKit(): Kit {
  if (kit) return kit;
  if (typeof document === "undefined") throw new Error("puppet: the kit needs a browser");
  const lengths: Record<string, number> = {};
  for (const [n, b] of Object.entries(RIG.bones)) lengths[n] = b.length;
  const pieces = bonePieces(lengths);
  const bones: Record<string, PieceKit> = {};
  let seed = 1;
  for (const n of RIG.drawOrder) bones[n] = buildPiece(pieces[n], seed++);

  // The split pin: a small domed brass head.
  const pin = new SphereGeometry(0.0082, 18, 8, 0, Math.PI * 2, 0, Math.PI / 2);
  pin.rotateX(Math.PI / 2); // dome toward +z
  pin.scale(1, 1, 0.34);
  const pinMaterial = new MeshStandardMaterial({ color: "#bf9c5c", metalness: 0.4, roughness: 0.45 });
  pinMaterial.name = "puppet:pin";
  kit = { bones, items: new Map(), pin, pinMaterial };
  return kit;
}

/** The shared pieces for every bone, keyed by bone name. */
export function getBoneKit(): Record<string, PieceKit> {
  return getKit().bones;
}

/** The shared pieces for a signature item kind (built on first request). */
export function getItemKit(kind: ItemKind): ItemPieceKit[] {
  const k = getKit();
  let list = k.items.get(kind);
  if (!list) {
    const socketY = RIG.sockets.head.at[1];
    list = itemPieces(kind, socketY).map((p, i) => ({
      ...buildPiece(p, 101 + i * 7 + kind.length * 13, p.rough ?? 1),
      tone: p.tone,
      layer: p.layer,
    }));
    k.items.set(kind, list);
  }
  return list;
}

export function getPin(): { geometry: SphereGeometry; material: MeshStandardMaterial } {
  const k = getKit();
  return { geometry: k.pin, material: k.pinMaterial };
}

/** Free every shared puppet resource. Only for full teardown. */
export function disposePuppetKit(): void {
  if (!kit) return;
  const geos: BufferGeometry[] = [kit.pin];
  const all: PieceKit[] = [...Object.values(kit.bones), ...[...kit.items.values()].flat()];
  for (const p of all) {
    geos.push(p.geometry, p.halo);
    p.haloMaterial.alphaMap?.dispose();
    p.haloMaterial.dispose();
  }
  geos.forEach((g) => g.dispose());
  kit.pinMaterial.dispose();
  kit = null;
}
