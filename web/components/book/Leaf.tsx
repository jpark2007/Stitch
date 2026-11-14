"use client";

/**
 * One leaf of the book: a skinned two-sided paper sheet with a chain of bones
 * along its width, hinged at the gutter.
 *
 * Each frame the leaf reads its commanded turn from the shared BookRuntime,
 * computes a target angle per bone (leafShape.turnTargets) and springs each
 * bone toward it. Springs are stiff at the hinge and softer toward the free
 * edge, so the edge trails a turn, swings past by a hair and settles, which
 * is what makes it read as paper rather than a rotating board. A twist about
 * the head edge lifts the bottom corner first, like a page peeled by its
 * corner.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import {
  Bone,
  BufferAttribute,
  BufferGeometry,
  FrontSide,
  MeshStandardMaterial,
  Skeleton,
  SkinnedMesh,
  Vector2,
  type Texture,
} from "three";
import { BONES, PAGE_H, PAGE_W, SEG, turnTargets, turnTwist } from "./leafShape";
import type { BookRuntime } from "./runtime";

const X_SEGS = BONES * 2;
const Z_SEGS = 2;
const SHEET_T = 0.0016;

/** Two sheets (recto facing +y, verso facing -y) in one skinned geometry. */
function buildLeafGeometry(): BufferGeometry {
  const cols = X_SEGS + 1;
  const rows = Z_SEGS + 1;
  const perSheet = cols * rows;
  const pos = new Float32Array(perSheet * 2 * 3);
  const uv = new Float32Array(perSheet * 2 * 2);
  const skinIndex = new Uint16Array(perSheet * 2 * 4);
  const skinWeight = new Float32Array(perSheet * 2 * 4);
  const index: number[] = [];

  for (let s = 0; s < 2; s++) {
    const y = s === 0 ? SHEET_T / 2 : -SHEET_T / 2;
    for (let iz = 0; iz < rows; iz++) {
      for (let ix = 0; ix < cols; ix++) {
        const k = s * perSheet + iz * cols + ix;
        const x = (ix / X_SEGS) * PAGE_W;
        const z = (iz / Z_SEGS) * PAGE_H;
        pos.set([x, y, z], k * 3);
        // Canvas top sits at the head edge (z = 0). The verso is read with the
        // hinge on its right, so its u runs from the free edge.
        const u = s === 0 ? x / PAGE_W : 1 - x / PAGE_W;
        uv.set([u, 1 - z / PAGE_H], k * 2);
        const xf = x / SEG;
        const b = Math.min(Math.floor(xf), BONES - 1);
        const f = Math.min(1, xf - b);
        skinIndex.set([b, b + 1, 0, 0], k * 4);
        skinWeight.set([1 - f, f, 0, 0], k * 4);
      }
    }
    for (let iz = 0; iz < Z_SEGS; iz++) {
      for (let ix = 0; ix < X_SEGS; ix++) {
        const a = s * perSheet + iz * cols + ix;
        const b = a + cols;
        const c = a + 1;
        const d = b + 1;
        if (s === 0) index.push(a, b, c, b, d, c);
        else index.push(a, c, b, b, c, d);
      }
    }
  }

  const g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(pos, 3));
  g.setAttribute("uv", new BufferAttribute(uv, 2));
  g.setAttribute("skinIndex", new BufferAttribute(skinIndex, 4));
  g.setAttribute("skinWeight", new BufferAttribute(skinWeight, 4));
  g.setIndex(index);
  const tris = Z_SEGS * X_SEGS * 6;
  g.addGroup(0, tris, 0);
  g.addGroup(tris, tris, 1);
  g.computeVertexNormals();
  return g;
}

export interface LeafTextures {
  /** Recto: faces up while the leaf rests on the right. */
  front: Texture;
  /** Verso: faces up once the leaf has turned to the left. */
  back: Texture;
  /** Shared deckled edge, so page edges are slightly irregular. */
  alphaMap: Texture;
  normalMap: Texture;
}

function pageMaterial(map: Texture, t: LeafTextures) {
  const m = new MeshStandardMaterial({
    map,
    normalMap: t.normalMap,
    normalScale: new Vector2(0.4, 0.4),
    // No roughness map: its soft fields read as stains under a spotlight.
    roughness: 0.94,
    metalness: 0,
    alphaMap: t.alphaMap,
    alphaTest: 0.5,
    alphaToCoverage: true,
    side: FrontSide,
  });
  m.name = "book:page";
  return m;
}

/* Springs: natural frequency (rad/s) at the hinge and at the free edge. */
const W_ROOT = 24;
const W_TIP = 13.5;
const ZETA = 0.7;
const W_TWIST = 15;
const MAX_STEP = 1 / 240;

/** The skinned sheet and its per-bone springs. */
export class LeafBody {
  readonly mesh: SkinnedMesh;
  private bones: Bone[] = [];
  private phi = new Float32Array(BONES);
  private vel = new Float32Array(BONES);
  private tw = new Float32Array(BONES);
  private twv = new Float32Array(BONES);
  private target = new Float32Array(BONES);
  private twTarget = new Float32Array(BONES);
  private started = false;
  private settled = false;
  private key = "";

  constructor(
    private readonly index: number,
    textures: LeafTextures,
  ) {
    const geometry = buildLeafGeometry();
    const materials = [pageMaterial(textures.front, textures), pageMaterial(textures.back, textures)];
    for (let i = 0; i <= BONES; i++) {
      const b = new Bone();
      b.rotation.order = "ZXY";
      if (i > 0) {
        b.position.x = SEG;
        this.bones[i - 1].add(b);
      }
      this.bones.push(b);
    }
    const mesh = new SkinnedMesh(geometry, materials);
    mesh.add(this.bones[0]);
    mesh.bind(new Skeleton(this.bones));
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    // Bones carry the sheet far outside its bind-pose bounds.
    mesh.frustumCulled = false;
    mesh.name = `leaf-${index}`;
    this.mesh = mesh;
  }

  /** Pose for this frame. Returns true while the sheet is still moving. */
  update(rt: BookRuntime, delta: number): boolean {
    const leaf = rt.leaves[this.index];
    const { mesh, phi, vel, tw, twv, target, twTarget } = this;
    mesh.visible = leaf.visible;
    mesh.position.set(0, rt.hinge, -PAGE_H / 2);
    if (!leaf.visible && this.started) return false;

    turnTargets(leaf, rt.restR, rt.restL, target);
    const twist = turnTwist(leaf);
    // Twist builds toward the free edge.
    const norm = ((BONES - 1) * BONES) / 2;
    for (let i = 0; i < BONES; i++) twTarget[i] = (twist * i) / norm;

    const key = `${leaf.p.toFixed(5)}|${leaf.mode}|${leaf.dir}|${rt.restVersion}|${twist.toFixed(5)}`;
    if (this.settled && key === this.key) return false;
    this.key = key;

    if (leaf.mode === "frozen" || !this.started) {
      phi.set(target);
      tw.set(twTarget);
      vel.fill(0);
      twv.fill(0);
      this.started = true;
    } else {
      const dt = Math.min(delta, 1 / 20);
      const steps = Math.max(1, Math.ceil(dt / MAX_STEP));
      const h = dt / steps;
      const stiff = leaf.stiff;
      const wt = W_TWIST * stiff;
      for (let s = 0; s < steps; s++) {
        for (let i = 0; i < BONES; i++) {
          const w = (W_ROOT + ((W_TIP - W_ROOT) * i) / (BONES - 1)) * stiff;
          vel[i] += (w * w * (target[i] - phi[i]) - 2 * ZETA * w * vel[i]) * h;
          phi[i] += vel[i] * h;
          twv[i] += (wt * wt * (twTarget[i] - tw[i]) - 2 * 0.85 * wt * twv[i]) * h;
          tw[i] += twv[i] * h;
        }
      }
      // Never sink into the stacks: clamp to the rest shapes on either side.
      for (let i = 0; i < BONES; i++) {
        const lo = rt.restR[i];
        const hi = Math.PI - rt.restL[i];
        if (phi[i] < lo) {
          phi[i] = lo;
          if (vel[i] < 0) vel[i] = 0;
        } else if (phi[i] > hi) {
          phi[i] = hi;
          if (vel[i] > 0) vel[i] = 0;
        }
      }
    }

    let err = 0;
    for (let i = 0; i < BONES; i++) {
      err = Math.max(err, Math.abs(target[i] - phi[i]), Math.abs(vel[i]) * 0.05);
      err = Math.max(err, Math.abs(twTarget[i] - tw[i]), Math.abs(twv[i]) * 0.05);
    }
    // Settled leaves skip all work until their command (the key) changes.
    this.settled = err < 2e-4;
    if (this.settled) {
      phi.set(target);
      tw.set(twTarget);
    }

    let prev = 0;
    for (let i = 0; i < BONES; i++) {
      this.bones[i].rotation.set(tw[i], 0, phi[i] - prev);
      prev = phi[i];
    }
    return !this.settled;
  }

  dispose() {
    this.mesh.geometry.dispose();
    (this.mesh.material as MeshStandardMaterial[]).forEach((m) => m.dispose());
    this.mesh.skeleton.dispose();
  }
}

export interface LeafProps {
  index: number;
  runtime: BookRuntime;
  textures: LeafTextures;
}

export function Leaf({ index, runtime, textures }: LeafProps) {
  const body = useMemo(() => new LeafBody(index, textures), [index, textures]);
  useEffect(() => () => body.dispose(), [body]);
  useFrame((state, delta) => {
    if (body.update(runtime, delta)) state.invalidate();
  });
  return <primitive object={body.mesh} />;
}

export default Leaf;
