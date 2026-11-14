/**
 * The page block on one side of the spine: a slab whose top dips into the
 * gutter, with a slightly uneven fore-edge. Positions are rewritten in place
 * when the block's thickness or the hinge height changes (only during turns
 * and the opening), so there is no per-frame allocation.
 *
 * Groups: 0 = top surface (paper), 1 = cut edges (page-edge stripes).
 */

import { BufferAttribute, BufferGeometry } from "three";
import { BLOCK_T, COVER_T, PAGE_H, PAGE_W, pageProfile } from "./leafShape";
import { rng } from "./pageArt";

const NX = 28;
const NZ = 14;
/** The block stops a hair short of the leaf so the top sheet's edge shows. */
const BLOCK_W = PAGE_W - 0.012;

export interface BlockGeometry {
  geometry: BufferGeometry;
  /** `top`: flat top height (book space); `hinge`: gutter height; `bow`: page swell. */
  update(top: number, hinge: number, bow?: number): void;
}

export function createBlockGeometry(sign: 1 | -1, seed: number): BlockGeometry {
  const xs = Array.from({ length: NX }, (_, i) => BLOCK_W * Math.pow(i / (NX - 1), 1.5));
  const zs = Array.from({ length: NZ }, (_, j) => -PAGE_H / 2 + (PAGE_H * j) / (NZ - 1));
  const r = rng(seed);
  // Hand-trimmed fore-edge: a few tenths of a millimetre of wobble.
  const jitter = zs.map(() => (r() - 0.5) * 0.006);

  const topCount = NX * NZ;
  const foreCount = 2 * NZ;
  const endCount = 2 * NX;
  const total = topCount + foreCount + endCount * 2;
  const pos = new Float32Array(total * 3);
  const uv = new Float32Array(total * 2);
  const idxTop: number[] = [];
  const idxEdge: number[] = [];

  // Triangles are wound for sign = +1 and mirrored for the left block.
  const tri = (list: number[], a: number, b: number, c: number) => {
    if (sign > 0) list.push(a, b, c);
    else list.push(a, c, b);
  };

  // Top: grid x by z, facing +y.
  for (let i = 0; i < NX - 1; i++) {
    for (let j = 0; j < NZ - 1; j++) {
      const a = i * NZ + j;
      const b = a + 1;
      const c = a + NZ;
      const d = c + 1;
      tri(idxTop, a, b, c);
      tri(idxTop, b, d, c);
    }
  }
  // Fore-edge: bottom/top pairs along z, facing +x (sign).
  const f0 = topCount;
  for (let j = 0; j < NZ - 1; j++) {
    const a = f0 + j * 2;
    const b = a + 1;
    const c = a + 2;
    const d = a + 3;
    tri(idxEdge, a, b, c);
    tri(idxEdge, b, d, c);
  }
  // Head (z-) and tail (z+): bottom/top pairs along x.
  const h0 = f0 + foreCount;
  const t0 = h0 + endCount;
  for (let i = 0; i < NX - 1; i++) {
    const a = h0 + i * 2;
    tri(idxEdge, a, a + 1, a + 2);
    tri(idxEdge, a + 1, a + 3, a + 2);
    const b = t0 + i * 2;
    tri(idxEdge, b, b + 2, b + 1);
    tri(idxEdge, b + 1, b + 2, b + 3);
  }

  const geometry = new BufferGeometry();
  const posAttr = new BufferAttribute(pos, 3);
  geometry.setAttribute("position", posAttr);
  geometry.setAttribute("uv", new BufferAttribute(uv, 2));
  geometry.setIndex([...idxTop, ...idxEdge]);
  geometry.addGroup(0, idxTop.length, 0);
  geometry.addGroup(idxTop.length, idxEdge.length, 1);

  let lastTop = NaN;
  let lastHinge = NaN;
  let lastBow = NaN;

  function update(top: number, hinge: number, bow = 0) {
    if (Math.abs(top - lastTop) < 1e-6 && Math.abs(hinge - lastHinge) < 1e-6 && Math.abs(bow - lastBow) < 1e-6) return;
    lastTop = top;
    lastHinge = hinge;
    lastBow = bow;
    const base = COVER_T;
    const depth = Math.max(0, top - hinge);
    const yAt = (x: number) => Math.max(base, hinge + pageProfile(x, depth, bow));
    const v = (y: number) => (y - base) / BLOCK_T;
    const xEnd = (j: number) => BLOCK_W + jitter[j];
    const put = (k: number, x: number, y: number, z: number, u: number, w: number) => {
      pos[k * 3] = sign * x;
      pos[k * 3 + 1] = y;
      pos[k * 3 + 2] = z;
      uv[k * 2] = u;
      uv[k * 2 + 1] = w;
    };
    for (let i = 0; i < NX; i++) {
      for (let j = 0; j < NZ; j++) {
        const x = i === NX - 1 ? xEnd(j) : xs[i];
        put(i * NZ + j, x, yAt(xs[i]), zs[j], xs[i] / PAGE_W, (zs[j] + PAGE_H / 2) / PAGE_H);
      }
    }
    const yTop = yAt(BLOCK_W);
    for (let j = 0; j < NZ; j++) {
      const u = (zs[j] / PAGE_H + 0.5) * 6;
      put(f0 + j * 2, xEnd(j), base, zs[j], u, 0);
      put(f0 + j * 2 + 1, xEnd(j), yTop, zs[j], u, v(yTop));
    }
    for (let i = 0; i < NX; i++) {
      const y = yAt(xs[i]);
      const xh = i === NX - 1 ? xEnd(0) : xs[i];
      const xt = i === NX - 1 ? xEnd(NZ - 1) : xs[i];
      const u = (xs[i] / PAGE_W) * 5;
      put(h0 + i * 2, xh, base, -PAGE_H / 2, u, 0);
      put(h0 + i * 2 + 1, xh, y, -PAGE_H / 2, u, v(y));
      put(t0 + i * 2, xt, base, PAGE_H / 2, u, 0);
      put(t0 + i * 2 + 1, xt, y, PAGE_H / 2, u, v(y));
    }
    posAttr.needsUpdate = true;
    (geometry.getAttribute("uv") as BufferAttribute).needsUpdate = true;
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();
  }

  return { geometry, update };
}
