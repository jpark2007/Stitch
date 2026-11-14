/**
 * The puppet as a plain three.js object tree, built from shared/rig/puppet.json:
 *
 *   root (hip: position, scale, mirror)
 *     body (centres the paper stack on the acting plane)
 *       bone groups, nested like the rig (pivot = position, rotation = z)
 *         piece mesh (extruded card), halo (soft shadow), split pin
 *         socket objects (hand_l, hand_r, head) and the item on the head
 *
 * Shared geometry comes from kit.ts and per-person materials from colors.ts,
 * so creating one allocates only Object3Ds. `apply()` is the only per-frame
 * call and allocates nothing.
 */

import { Group, Mesh, Object3D } from "three";
import type { ItemKind } from "./items";
import type { BodyTone, PuppetMaterials } from "./colors";
import { getBoneKit, getItemKit, getPin, getShoe, LAYER_GAP, THICKNESS, type ItemPieceKit } from "./kit";
import { BONES, RIG, SOCKETS, type SocketName } from "./rig";
import type { Rotations } from "./kinematics";

/** Which paper each bone is cut from: far limbs in shade, head and hands lighter. */
function toneOf(bone: string): BodyTone {
  if (bone === "head" || bone === "hand_r") return "light";
  if (bone === "hand_l") return "farLight";
  if (bone.startsWith("arm_l") || bone.startsWith("leg_l")) return "far";
  return "body";
}

/** Gap between an item's own stacked pieces. */
const ITEM_GAP = 0.0042;
/** Halos sit just behind their piece, in the gap above the piece below. */
const HALO_BEHIND = 0.0022;
/**
 * Where each piece's soft shadow falls on the piece behind it, in rig units,
 * in the world's directions (down and to the right, from a key light at the
 * reader's upper left). Held there whatever the bone's angle or facing.
 */
const HALO_X = 0.0032;
const HALO_Y = -0.0045;

interface Halo {
  mesh: Mesh;
  /** Index into BONES of the bone the halo rides on. */
  bone: number;
  /** Offset strength (thin pieces stacked close cast shorter shadows). */
  k: number;
}

export class PuppetObject {
  readonly root = new Group();
  readonly bones: Record<string, Group> = {};
  readonly sockets = {} as Record<SocketName, Object3D>;
  private readonly body = new Group();
  private readonly pieces: { mesh: Mesh; tone: BodyTone }[] = [];
  private readonly shadowCasters: Mesh[] = [];
  private readonly itemGroup = new Group();
  private itemMeshes: { mesh: Mesh; piece: ItemPieceKit }[] = [];
  private itemKind: ItemKind | null = null;
  private readonly boneList: Group[] = [];
  private readonly parentIndex: number[] = [];
  private readonly absAngle = new Float64Array(BONES.length);
  private readonly halos: Halo[] = [];
  private itemHalos: Halo[] = [];

  constructor(name = "puppet") {
    this.root.name = name;
    this.root.add(this.body);
    const kit = getBoneKit();
    const pin = getPin();
    const layerOf = (b: string) => Math.max(0, RIG.drawOrder.indexOf(b));
    const depth = RIG.drawOrder.length * LAYER_GAP;
    this.body.position.z = -depth / 2;

    for (const n of BONES) {
      const def = RIG.bones[n];
      const g = new Group();
      g.name = n;
      g.position.set(def.pivot[0], def.pivot[1], 0);
      (def.parent === RIG.root ? this.body : this.bones[def.parent]).add(g);
      this.bones[n] = g;
      this.boneList.push(g);
      this.parentIndex.push(BONES.indexOf(def.parent));
      const bi = this.boneList.length - 1;

      const z = layerOf(n) * LAYER_GAP;
      const pk = kit[n];
      const mesh = new Mesh(pk.geometry);
      mesh.name = `${n}:piece`;
      mesh.position.z = z;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      g.add(mesh);
      this.pieces.push({ mesh, tone: toneOf(n) });
      this.shadowCasters.push(mesh);

      const halo = new Mesh(pk.halo, pk.haloMaterial);
      halo.name = `${n}:halo`;
      halo.position.z = z - HALO_BEHIND;
      g.add(halo);
      this.halos.push({ mesh: halo, bone: bi, k: 1 });

      if (n === "leg_l.lower" || n === "leg_r.lower") {
        // A paper shoe glued over the foot.
        const shoe = getShoe();
        const s = new Mesh(shoe.piece.geometry, [n === "leg_l.lower" ? shoe.far : shoe.near, shoe.edge]);
        s.name = `${n}:shoe`;
        s.position.z = z + THICKNESS + 0.0016;
        s.castShadow = true;
        s.receiveShadow = true;
        const sh = new Mesh(shoe.piece.halo, shoe.piece.haloMaterial);
        sh.position.z = z + THICKNESS + 0.0008;
        g.add(sh, s);
        this.halos.push({ mesh: sh, bone: bi, k: 0.5 });
        this.shadowCasters.push(s);
      }

      // Split pin through this piece and the one it hangs from, its head on
      // whichever of the two is nearer the viewer. (Legs hang from the hip,
      // which on the doll is the torso card.)
      if (n !== "torso") {
        const parent = def.parent === RIG.root ? "torso" : def.parent;
        const top = Math.max(layerOf(n), layerOf(parent));
        const p = new Mesh(pin.geometry, pin.material);
        p.name = `${n}:pin`;
        p.position.z = top * LAYER_GAP + THICKNESS + 0.0002;
        g.add(p);
      }
    }

    for (const s of SOCKETS) {
      const def = RIG.sockets[s];
      const o = new Object3D();
      o.name = `socket:${s}`;
      o.position.set(def.at[0], def.at[1], layerOf(def.bone) * LAYER_GAP + THICKNESS / 2);
      this.bones[def.bone].add(o);
      this.sockets[s] = o;
    }
    this.itemGroup.name = "item";
    this.itemGroup.position.z = THICKNESS / 2 + 0.0022;
    this.sockets.head.add(this.itemGroup);
  }

  setMaterials(m: PuppetMaterials): void {
    for (const p of this.pieces) p.mesh.material = [m.body[p.tone], m.edge];
    for (const { mesh, piece } of this.itemMeshes) mesh.material = [m.item[piece.tone], m.itemEdge];
  }

  /** Swap the signature item (null removes it). Call setMaterials after. */
  setItem(kind: ItemKind | null): void {
    if (kind === this.itemKind) return;
    this.itemKind = kind;
    this.itemGroup.clear();
    this.itemMeshes = [];
    this.itemHalos = [];
    if (!kind) return;
    const head = BONES.indexOf(RIG.sockets.head.bone);
    for (const piece of getItemKit(kind)) {
      const z = piece.layer * ITEM_GAP;
      const mesh = new Mesh(piece.geometry);
      mesh.position.z = z;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      const halo = new Mesh(piece.halo, piece.haloMaterial);
      halo.position.z = z - HALO_BEHIND * 0.6;
      this.itemGroup.add(halo, mesh);
      this.itemHalos.push({ mesh: halo, bone: head, k: 0.6 });
      this.itemMeshes.push({ mesh, piece });
    }
  }

  setCastShadow(on: boolean): void {
    for (const m of this.shadowCasters) m.castShadow = on;
    for (const { mesh } of this.itemMeshes) mesh.castShadow = on;
  }

  /**
   * Pose the puppet. `x, y, z` is root.position (stage units, the ground
   * under the hip); `facing` ±1 mirrors about the vertical axis; `scale` is
   * stage units per rig unit; `rot` holds bone rotations (missing = 0).
   */
  apply(x: number, y: number, z: number, facing: number, scale: number, rot: Rotations): void {
    this.root.position.set(x, y + RIG.hipHeight * scale, z);
    this.root.scale.set(facing < 0 ? -scale : scale, scale, scale);
    for (let i = 0; i < BONES.length; i++) {
      const v = rot[BONES[i]];
      const r = typeof v === "number" && Number.isFinite(v) ? v : 0;
      this.boneList[i].rotation.z = r;
      const p = this.parentIndex[i];
      this.absAngle[i] = (p >= 0 ? this.absAngle[p] : 0) + r;
    }
    // Keep every soft shadow falling the same way in the world.
    const hx = facing < 0 ? -HALO_X : HALO_X;
    this.placeHalos(this.halos, hx);
    this.placeHalos(this.itemHalos, hx);
  }

  private placeHalos(list: Halo[], hx: number): void {
    for (const h of list) {
      const a = this.absAngle[h.bone];
      const c = Math.cos(a);
      const s = Math.sin(a);
      h.mesh.position.x = (hx * c + HALO_Y * s) * h.k;
      h.mesh.position.y = (-hx * s + HALO_Y * c) * h.k;
    }
  }
}
