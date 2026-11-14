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
 * Paper: the puppet stands up with the page (./standing), lying flat while
 * the set is folded and rising with the layer behind it, or riding the riser
 * it stands on. It registers its handle with the provider so props can sit
 * in its hands (grip on hand_l / hand_r) or on its head (anchor), and is cut
 * off at the wings so entrances slide in from the set's edge.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import type { Group } from "three";
import type { Person } from "@/lib/types";
import { Puppet, scaleForBuild, type PuppetHandle, type PuppetInputs } from "../Puppet";
import { BONES, RIG } from "../puppet/rig";
import type { Rotations } from "../puppet/kinematics";
import { FootPlanter } from "./planting";
import { PRIORITY, standUp, useScene, useWingClip } from "./SceneProvider";
import type { Mount } from "./standing";

/**
 * Puppets share the acting plane (z = 0.5). Where two overlap, a few
 * millimetres between them keeps their paper from z-fighting; nobody can see
 * the difference.
 */
const DEPTH_STAGGER = 0.012;

export interface PuppetActorProps {
  /** Person id: the actor plays target `puppet:<id>`. */
  id: string;
  /** Override the person from the scene data (default: looked up by id). */
  person?: Person;
  /** Order among the cast, for the small depth stagger. Default 0. */
  index?: number;
}

interface Driver extends PuppetInputs {
  position: [number, number, number];
  rot: Rotations;
}

export function PuppetActor({ id, person: given, index = 0 }: PuppetActorProps) {
  const { frame, scene, feetPlanted, runtime, standing, person: lookup } = useScene();
  const person = given ?? lookup(id);
  const character = person.character;
  const scale = scaleForBuild(character.build);
  const dz = index * DEPTH_STAGGER;

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
  const outer = useRef<Group>(null);
  const inner = useRef<Group>(null);
  const mount = useMemo<Mount>(() => ({ angle: 0, dy: 0, dz: 0 }), []);
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
    const p = pf.position;
    d.position[0] = p[0];
    d.position[1] = p[1];
    d.position[2] = p[2] + dz;
    // Picked up off its layer by the host (runtime.lifts; none by default).
    const lift = runtime.lifts.size > 0 ? runtime.lifts.get(pf.target) : undefined;
    if (lift) {
      d.position[0] += lift.x;
      d.position[1] += lift.y;
      d.position[2] += lift.z;
    }
    d.facing = pf.facing;
    if (planter) d.position[1] -= planter.drop(frame.t, readRot, scale);
    // Hinge about the surface the timeline stands it on (not the planted hip).
    standUp(standing, outer.current, inner.current, p[0], p[1], p[2] + dz, mount);
  }, PRIORITY.drive);

  useWingClip(() => handle.current?.object);

  return (
    <group ref={outer}>
      <group ref={inner}>
        <Puppet
          ref={handle}
          character={character}
          personId={id}
          driver={driver}
          // A real prop worn in the head socket replaces the drawn item.
          showItem={!character.itemPropId}
        />
      </group>
    </group>
  );
}
