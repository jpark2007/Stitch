"use client";

/**
 * <PuppetActor>: one cast member, driven by its `puppet:<personId>` tracks.
 *
 * Binds once (per timeline): the puppet's frame slot, and for every rig bone
 * its rotation slot in the shared frame. Then, every frame, copies
 * root.position, root.facing and the bone rotations into the <Puppet>'s
 * mutable driver: no allocation, no React render.
 *
 * Feet: when the timeline is not planted (Timeline.feetPlanted not true) the
 * actor lowers the hip onto the feet on walk and run stretches (./planting);
 * when it is, root.position is used as is.
 *
 * It registers its handle with the provider so props can sit in its hands
 * (grip on hand_l / hand_r) or on its head (anchor), and is cut off at the
 * wings so entrances slide in from the set's edge.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import type { Person } from "@/lib/types";
import { Puppet, scaleForBuild, type PuppetHandle, type PuppetInputs } from "../Puppet";
import { BONES, RIG } from "../puppet/rig";
import type { Rotations } from "../puppet/kinematics";
import { FootPlanter } from "./planting";
import { PRIORITY, useScene, useWingClip } from "./SceneProvider";

export interface PuppetActorProps {
  /** Person id: the actor plays target `puppet:<id>`. */
  id: string;
  /** Override the person from the scene data (default: looked up by id). */
  person?: Person;
}

interface Driver extends PuppetInputs {
  position: [number, number, number];
  rot: Rotations;
}

export function PuppetActor({ id, person: given }: PuppetActorProps) {
  const { frame, scene, feetPlanted, runtime, person: lookup } = useScene();
  const person = given ?? lookup(id);
  const character = person.character;
  const scale = scaleForBuild(character.build);

  // Bind once: frame slot, bone slots and (when needed) the planter.
  const bind = useMemo(() => {
    const pf = frame.puppetsById.get(id) ?? null;
    const slots = Int32Array.from(BONES, (b) => pf?.boneIndex.get(b) ?? -1);
    let planter: FootPlanter | null = null;
    if (!feetPlanted && pf) {
      const track = scene.timeline.tracks.find((t) => t.target === pf.target && t.channel === "root.position");
      const p = new FootPlanter(RIG, track?.keys ?? []);
      if (!p.idle) planter = p;
    }
    return { pf, slots, planter };
  }, [frame, id, feetPlanted, scene.timeline]);

  const driver = useRef<Driver>({ position: [0, 0, 0.5], facing: 1, rot: {} });
  const handle = useRef<PuppetHandle>(null);
  useEffect(() => runtime.registerPuppet(id, handle), [runtime, id]);

  const readRot = useMemo(() => (bone: string) => driver.current.rot[bone] ?? 0, []);

  useFrame(() => {
    const { pf, slots, planter } = bind;
    if (!pf) return;
    const d = driver.current;
    const rot = d.rot;
    const r = pf.rotations;
    for (let i = 0; i < slots.length; i++) {
      const s = slots[i];
      rot[BONES[i]] = s >= 0 ? r[s] : 0;
    }
    d.position[0] = pf.position[0];
    d.position[1] = pf.position[1];
    d.position[2] = pf.position[2];
    d.facing = pf.facing;
    if (planter) d.position[1] -= planter.drop(frame.t, readRot, scale);
  }, PRIORITY.drive);

  useWingClip(() => handle.current?.object);

  return (
    <Puppet
      ref={handle}
      character={character}
      personId={id}
      driver={driver}
      // A real prop worn in the head socket replaces the drawn item.
      showItem={!character.itemPropId}
    />
  );
}
