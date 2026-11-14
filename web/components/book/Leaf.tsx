"use client";

/**
 * One leaf of the book: a skinned two-sided paper sheet with a chain of bones
 * along its width, hinged at the gutter.
 *
 * Each frame the leaf reads its commanded turn from the shared BookRuntime,
 * computes a target angle per bone (leafShape.turnTargets) and springs each
 * bone toward it (leafSprings.ts): the free edge trails a turn, swings past
 * by a hair and settles. A twist about the head edge lifts the bottom corner
 * first, like a page peeled by its corner.
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
import { LeafSprings, twistTargets } from "./leafSprings";
import type { BookRuntime } from "./runtime";
import { FRONT_OFFSET } from "./stacking";

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
  /**
   * Deckled edge for each side, so the head, tail and fore-edge are slightly
   * irregular. The gutter edge is a clean fold (the maps are shifted so it
   * samples inside the deckle).
   */
  alphaFront: Texture;
  alphaBack: Texture;
  normalMap: Texture;
}

function pageMaterial(map: Texture, alphaMap: Texture, t: LeafTextures) {
  const m = new MeshStandardMaterial({
    map,
    normalMap: t.normalMap,
    normalScale: new Vector2(0.4, 0.4),
    // No roughness map: its soft fields read as stains under a spotlight.
    roughness: 0.94,
    metalness: 0,
    alphaMap,
    alphaTest: 0.5,
    alphaToCoverage: true,
    side: FrontSide,
  });
  m.name = "book:page";
  return m;
}

/** The skinned sheet and its per-bone springs. */
export class LeafBody {
  readonly mesh: SkinnedMesh;
  private bones: Bone[] = [];
  private springs = new LeafSprings(BONES, SEG, PAGE_H);
  private target = new Float32Array(BONES);
  private twTarget = new Float32Array(BONES);
  private started = false;
  private settled = false;
  /** Skipped while hidden, so its pose may be stale. */
  private hidden = false;
  /** The command the pose was last computed for. */
  private readonly last = { p: NaN, mode: "", dir: 0, rest: -1, twist: NaN };
  private front = false;

  constructor(
    private readonly index: number,
    textures: LeafTextures,
  ) {
    const geometry = buildLeafGeometry();
    const materials = [
      pageMaterial(textures.front, textures.alphaFront, textures),
      pageMaterial(textures.back, textures.alphaBack, textures),
    ];
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
    const { mesh, springs, target, twTarget } = this;
    mesh.visible = leaf.visible;
    mesh.position.set(0, rt.hinge, -PAGE_H / 2);
    // A moving sheet can lie exactly on a stack's top sheet (same hinge, same
    // rest shape); bias its depth so it, not the stale page, is seen.
    if (leaf.front !== this.front) {
      this.front = leaf.front;
      for (const m of mesh.material as MeshStandardMaterial[]) {
        m.polygonOffset = leaf.front;
        m.polygonOffsetFactor = leaf.front ? FRONT_OFFSET.factor : 0;
        m.polygonOffsetUnits = leaf.front ? FRONT_OFFSET.units : 0;
      }
    }
    if (!leaf.visible && this.started) {
      this.hidden = true;
      return false;
    }
    // A sheet back in view on a stack (the stack top again once the sheet
    // over it lifts off, or pulled back out of a landing) takes its rest
    // shape at once: its last pose went stale while it was hidden.
    const reshown = this.hidden && leaf.mode === "rest";
    this.hidden = false;

    turnTargets(leaf, rt.restR, rt.restL, target);
    const twist = turnTwist(leaf);
    twistTargets(twist, twTarget);

    // Settled leaves skip all work until their command changes (compared
    // field by field: a string key allocated every frame for every leaf).
    const same =
      leaf.p === this.last.p &&
      leaf.mode === this.last.mode &&
      leaf.dir === this.last.dir &&
      rt.restVersion === this.last.rest &&
      twist === this.last.twist;
    if (this.settled && same) return false;
    const last = this.last;
    last.p = leaf.p;
    last.mode = leaf.mode;
    last.dir = leaf.dir;
    last.rest = rt.restVersion;
    last.twist = twist;

    if (leaf.mode === "frozen" || !this.started || reshown) {
      springs.snap(target, twTarget);
      this.started = true;
    } else {
      springs.step(target, twTarget, leaf.stiff, delta, rt.restR, rt.restL);
    }

    this.settled = springs.error(target, twTarget) < 2e-4;
    if (this.settled) springs.snap(target, twTarget);

    const { phi, tw } = springs;
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
