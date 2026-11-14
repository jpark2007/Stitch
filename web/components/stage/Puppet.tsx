"use client";

/**
 * <Puppet>: the rigged, faceless paper puppet (shared/CONTRACTS.md "The
 * puppet", shared/rig/puppet.json). Flat card pieces with hand-cut edges,
 * joined at every pivot by a brass split pin, each casting a soft shadow on
 * the piece behind it. Coloured from the person's character sheet; the
 * signature item (character.item) is cut from paper and worn on the head.
 *
 * Driving it
 *   Static:     <Puppet character={c} position={[x, y, z]} facing={1} rot={{ head: -0.4 }} />
 *   Per frame:  pass `driver` (a ref to { position, facing, rot }) or mutate the
 *               `rot` object in place. Both are read every frame inside
 *               useFrame, so a timeline player never re-renders React.
 *   Units:      position is root.position (stage units, the ground under the
 *               hip); rotations are radians, unmirrored local frame; facing
 *               −1 mirrors about the puppet's vertical axis.
 *
 * Sockets (for props)
 *   const ref = useRef<PuppetHandle>(null);
 *   <Puppet ref={ref} ... />
 *   ref.current.socketWorldPosition("hand_r", v)   // THREE.Vector3, fresh this frame
 *   ref.current.socketWorldMatrix("head", m)       // includes scale and mirror
 *   ref.current.socket("hand_r")                   // the live Object3D
 *   The socket getters re-apply the latest inputs first, so they are correct
 *   whatever order useFrame callbacks run in.
 */

import { useFrame } from "@react-three/fiber";
import { useImperativeHandle, useLayoutEffect, useMemo, useRef, type Ref, type RefObject } from "react";
import type { Group, Matrix4, Object3D, Vector3 } from "three";
import type { CharacterSheet } from "@/lib/types";
import { usePuppetMaterials } from "./puppet/colors";
import { pickItem } from "./puppet/items";
import type { Rotations } from "./puppet/kinematics";
import { PuppetObject } from "./puppet/PuppetObject";
import { scaleForBuild, type SocketName } from "./puppet/rig";

export type { Rotations } from "./puppet/kinematics";
export type { SocketName } from "./puppet/rig";

export type Vec3 = readonly [number, number, number] | [number, number, number];

/** Everything a timeline drives on a puppet (timeline tracks root.position, root.facing, <bone>.rot). */
export interface PuppetInputs {
  position: Vec3;
  facing: number;
  rot: Rotations;
}

export interface PuppetHandle {
  /** The puppet's root object (at the hip, scaled and mirrored). */
  readonly object: Group;
  /** Stage units per rig unit for this puppet's build. */
  readonly scale: number;
  /** The live socket object. Its matrixWorld is fresh after sync(). */
  socket(name: SocketName): Object3D;
  /** World position of a socket, after applying the latest inputs. */
  socketWorldPosition(name: SocketName, out: Vector3): Vector3;
  /** World matrix of a socket (includes the puppet's scale and mirror). */
  socketWorldMatrix(name: SocketName, out: Matrix4): Matrix4;
  /** Apply the latest inputs now and update world matrices. */
  sync(): void;
}

export interface PuppetProps {
  /** The person's character sheet: color, build, item. */
  character: Pick<CharacterSheet, "color"> & Partial<Pick<CharacterSheet, "build" | "item">>;
  /** Names the object tree (`puppet:<personId>`), for debugging and picking. */
  personId?: string;
  /** root.position, stage units. Default [0, 0, 0.5] (the acting plane). */
  position?: Vec3;
  /** 1 faces right (+x), −1 faces left. */
  facing?: number;
  /** Bone rotations. Read every frame, so it may be mutated in place. */
  rot?: Rotations;
  /** Per-frame inputs; when set (and non-null) they win over the props above. */
  driver?: RefObject<PuppetInputs | null>;
  /** Cast real shadows onto the set. Default true. */
  castShadow?: boolean;
  ref?: Ref<PuppetHandle>;
}

const ACTING_PLANE: Vec3 = [0, 0, 0.5];
const NO_ROT: Rotations = {};

export function Puppet({
  character,
  personId,
  position = ACTING_PLANE,
  facing = 1,
  rot = NO_ROT,
  driver,
  castShadow = true,
  ref,
}: PuppetProps) {
  const item = useMemo(() => pickItem(character.item), [character.item]);
  const mats = usePuppetMaterials(character.color, item.kind, item.color);
  const scale = scaleForBuild(character.build);
  const puppet = useMemo(() => new PuppetObject(personId ? `puppet:${personId}` : "puppet"), [personId]);

  // Latest props, read by the frame loop and the handle (written after render).
  const props = useRef({ position, facing, rot, driver, scale });
  useLayoutEffect(() => {
    props.current = { position, facing, rot, driver, scale };
  });

  useLayoutEffect(() => {
    puppet.setItem(item.kind);
    puppet.setMaterials(mats);
  }, [puppet, item.kind, mats]);

  useLayoutEffect(() => {
    puppet.setCastShadow(castShadow);
  }, [puppet, castShadow, item.kind]);

  const { handle, apply } = useMemo(() => {
    /** Copy the latest inputs onto the object tree (no matrix update). */
    const apply = () => {
      const p = props.current;
      const d = p.driver?.current;
      const pos = d ? d.position : p.position;
      puppet.apply(pos[0], pos[1], pos[2], d ? d.facing : p.facing, p.scale, d ? d.rot : p.rot);
    };
    const handle: PuppetHandle = {
      object: puppet.root,
      get scale() {
        return props.current.scale;
      },
      socket: (name) => puppet.sockets[name],
      socketWorldPosition(name, out) {
        apply();
        return puppet.sockets[name].getWorldPosition(out);
      },
      socketWorldMatrix(name, out) {
        apply();
        const s = puppet.sockets[name];
        s.updateWorldMatrix(true, false);
        return out.copy(s.matrixWorld);
      },
      sync() {
        apply();
        puppet.root.updateWorldMatrix(true, true);
      },
    };
    return { handle, apply };
  }, [puppet]);

  useImperativeHandle(ref, () => handle, [handle]);

  // Pose before the first frame renders (and whenever props change).
  useLayoutEffect(() => {
    apply();
  });

  useFrame(apply);

  return <primitive object={puppet.root} />;
}

export default Puppet;
